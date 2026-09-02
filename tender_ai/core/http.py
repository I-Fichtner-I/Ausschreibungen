"""Asynchroner HTTP-Client mit Retry, Backoff, Rate-Limit, Cache und robots.txt.

Alle Netzwerkzugriffe des Tools laufen ueber diese Klasse. Dadurch gelten die
Compliance- und Robustheitsregeln (hoefliches Rate-Limit, robots.txt,
Wiederholungen mit Exponential Backoff, Timeouts, Logging) an genau einer
Stelle und nicht verstreut in jedem Adapter.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from ..config import HttpConfig
from .cache import ResponseCache
from .errors import HttpError, RobotsDisallowedError
from .logging import get_logger
from .ratelimit import RateLimiter
from .robots import RobotsGuard

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class HttpStats:
    requests: int = 0
    cache_hits: int = 0
    retries: int = 0
    failures: int = 0
    bytes_downloaded: int = 0
    by_host: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "failures": self.failures,
            "bytes_downloaded": self.bytes_downloaded,
            "by_host": dict(self.by_host),
        }


class HttpClient:
    """Duenne, aber strenge Huelle um ``httpx.AsyncClient``."""

    def __init__(
        self,
        config: HttpConfig,
        *,
        cache: ResponseCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.cache = cache
        self.stats = HttpStats()
        self.rate_limiter = RateLimiter(config.requests_per_second)
        self.robots = RobotsGuard(config.user_agent, enabled=config.respect_robots)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            headers={"User-Agent": config.user_agent, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
            transport=transport,
        )

    # --- Lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Hooks (in Tests ueberschreibbar) ----------------------------------
    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    # --- Kern --------------------------------------------------------------
    def configure_host_rate(self, url: str, requests_per_second: float | None) -> None:
        self.rate_limiter.configure_host(urlsplit(url).netloc, requests_per_second)

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.config.backoff_max)
            except ValueError:
                pass
        delay = self.config.backoff_base * (2**attempt)
        delay = min(delay, self.config.backoff_max)
        return delay * (0.5 + random.random() / 2)  # Jitter gegen Thundering Herd

    async def _fetch_robots(self, url: str) -> httpx.Response | None:
        """robots.txt ueber den regulaeren Pfad holen (Rate-Limit, Retry, Cache).

        Nicht abrufbar (404, 5xx nach Wiederholungen, Netzfehler) -> ``None``;
        die Interpretation ("keine Aussage -> erlaubt") trifft der RobotsGuard.
        """
        try:
            return await self.request("GET", url, check_robots=False)
        except HttpError as exc:
            if exc.status_code in (401, 403, 404, 410):
                log.debug("robots_absent", url=url, status=exc.status_code)
            else:
                log.warning("robots_unreachable", url=url, error=str(exc))
            return None
        except RobotsDisallowedError:  # pragma: no cover - check_robots=False schliesst das aus
            return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        check_robots: bool | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            method.upper(), url, params=params, json=json, headers=headers
        )
        full_url = str(request.url)
        body = request.content or None

        cache_key = ResponseCache.make_key(method, full_url, body)
        if use_cache and self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.stats.cache_hits += 1
                log.debug("http_cache_hit", url=full_url)
                return httpx.Response(
                    status_code=cached["status_code"],
                    content=cached["content"],
                    headers=cached.get("headers") or {},
                    request=request,
                )

        should_check_robots = self.config.respect_robots if check_robots is None else check_robots
        if should_check_robots and not await self.robots.allowed(full_url, self._fetch_robots):
            raise RobotsDisallowedError(full_url)

        host = request.url.host or ""
        crawl_delay = self.robots.crawl_delay(full_url)
        if crawl_delay:
            # robots.txt darf strenger sein als unsere eigene Konfiguration -
            # aber nie lockerer (tighten statt configure).
            self.rate_limiter.tighten_host(host, 1.0 / crawl_delay)

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            await self.rate_limiter.acquire(host)
            self.stats.requests += 1
            self.stats.by_host[host] = self.stats.by_host.get(host, 0) + 1
            try:
                response = await self._client.send(request)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                delay = self._backoff_delay(attempt, None)
                self.stats.retries += 1
                log.warning(
                    "http_retry",
                    url=full_url,
                    attempt=attempt + 1,
                    error=str(exc),
                    delay=round(delay, 2),
                )
                await self._sleep(delay)
                request = self._client.build_request(
                    method.upper(), url, params=params, json=json, headers=headers
                )
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                delay = self._backoff_delay(attempt, response.headers.get("Retry-After"))
                self.stats.retries += 1
                log.warning(
                    "http_retry_status",
                    url=full_url,
                    status=response.status_code,
                    attempt=attempt + 1,
                    delay=round(delay, 2),
                )
                await response.aclose()
                await self._sleep(delay)
                request = self._client.build_request(
                    method.upper(), url, params=params, json=json, headers=headers
                )
                continue

            await response.aread()
            self.stats.bytes_downloaded += len(response.content)
            if use_cache and self.cache is not None and response.status_code < 400:
                self.cache.set(
                    cache_key,
                    status_code=response.status_code,
                    content=response.content,
                    headers={"Content-Type": response.headers.get("Content-Type", "")},
                )
            if response.status_code >= 400:
                self.stats.failures += 1
                raise HttpError(
                    full_url,
                    f"HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )
            return response

        self.stats.failures += 1
        raise HttpError(
            full_url,
            f"Abruf nach {self.config.max_retries + 1} Versuchen fehlgeschlagen: {last_error}",
        )

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
        expected_types: Collection[str] | None = None,
        check_robots: bool | None = None,
    ) -> Path:
        """Datei herunterladen (Ausschreibungsunterlagen).

        Wird gestreamt statt komplett in den Speicher geladen: Vergabeunterlagen
        koennen gross sein, und eine falsch verlinkte Datei darf den Lauf nicht
        sprengen. Ueberschreitet die Antwort ``max_bytes``, wird abgebrochen und
        die Teildatei entfernt - es bleibt nie eine halbe Datei liegen.
        """
        limit = self.config.max_download_bytes if max_bytes is None else max_bytes
        should_check_robots = self.config.respect_robots if check_robots is None else check_robots
        if should_check_robots and not await self.robots.allowed(url, self._fetch_robots):
            raise RobotsDisallowedError(url)

        host = urlsplit(url).netloc
        await self.rate_limiter.acquire(host)
        self.stats.requests += 1
        self.stats.by_host[host] = self.stats.by_host.get(host, 0) + 1

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        written = 0
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    self.stats.failures += 1
                    raise HttpError(
                        url, f"HTTP {response.status_code}", status_code=response.status_code
                    )
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
                if expected_types is not None and content_type not in expected_types:
                    raise HttpError(
                        url,
                        f"Unerwarteter Content-Type '{content_type}' "
                        f"(erwartet: {', '.join(sorted(expected_types))})",
                    )
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(65_536):
                        written += len(chunk)
                        if limit and written > limit:
                            raise HttpError(
                                url, f"Download ueberschreitet max_download_bytes ({limit} Bytes)"
                            )
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            self.stats.failures += 1
            raise HttpError(url, f"Download fehlgeschlagen: {exc}") from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

        self.stats.bytes_downloaded += written
        partial.replace(destination)
        return destination


def build_http_client(
    config: HttpConfig,
    cache_dir: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpClient:
    cache = (
        ResponseCache(cache_dir, ttl_seconds=config.cache_ttl_seconds, enabled=True)
        if config.cache_enabled and cache_dir is not None
        else None
    )
    return HttpClient(config, cache=cache, transport=transport)
