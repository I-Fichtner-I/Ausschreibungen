from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from tender_ai.config import HttpConfig
from tender_ai.core.cache import ResponseCache
from tender_ai.core.errors import HttpError, RobotsDisallowedError
from tender_ai.core.http import HttpClient, build_http_client
from tender_ai.core.ratelimit import RateLimiter


def config(**overrides) -> HttpConfig:
    base = dict(
        max_retries=2,
        backoff_base=0.001,
        backoff_max=0.01,
        requests_per_second=0,
        respect_robots=False,
        cache_enabled=False,
    )
    base.update(overrides)
    return HttpConfig(**base)


async def _no_sleep(_seconds: float) -> None:
    return None


@respx.mock
async def test_retries_on_server_error_then_succeeds():
    route = respx.get("https://api.test.invalid/data").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(500),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = HttpClient(config())
    client._sleep = _no_sleep  # type: ignore[method-assign]
    try:
        response = await client.get("https://api.test.invalid/data")
        assert response.json() == {"ok": True}
        assert route.call_count == 3
        assert client.stats.retries == 2
    finally:
        await client.aclose()


@respx.mock
async def test_gives_up_after_max_retries():
    respx.get("https://api.test.invalid/down").mock(return_value=httpx.Response(500))
    client = HttpClient(config(max_retries=1))
    client._sleep = _no_sleep  # type: ignore[method-assign]
    try:
        with pytest.raises(HttpError) as excinfo:
            await client.get("https://api.test.invalid/down")
        assert excinfo.value.status_code == 500
        assert client.stats.failures == 1
    finally:
        await client.aclose()


@respx.mock
async def test_retry_after_header_is_respected():
    respx.get("https://api.test.invalid/limited").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.01"}),
            httpx.Response(200, text="ok"),
        ]
    )
    client = HttpClient(config())
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    client._sleep = record  # type: ignore[method-assign]
    try:
        response = await client.get("https://api.test.invalid/limited")
        assert response.text == "ok"
        assert delays == [0.01]
    finally:
        await client.aclose()


@respx.mock
async def test_network_error_is_retried():
    respx.get("https://api.test.invalid/flaky").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, text="ok")]
    )
    client = HttpClient(config())
    client._sleep = _no_sleep  # type: ignore[method-assign]
    try:
        assert (await client.get("https://api.test.invalid/flaky")).text == "ok"
    finally:
        await client.aclose()


@respx.mock
async def test_robots_disallow_blocks_request():
    respx.get("https://portal.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /geschuetzt")
    )
    page = respx.get("https://portal.test.invalid/geschuetzt/liste")
    client = HttpClient(config(respect_robots=True))
    try:
        with pytest.raises(RobotsDisallowedError):
            await client.get("https://portal.test.invalid/geschuetzt/liste")
        assert page.call_count == 0
    finally:
        await client.aclose()


@respx.mock
async def test_robots_allows_other_paths():
    respx.get("https://portal.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /geschuetzt")
    )
    respx.get("https://portal.test.invalid/offen/liste").mock(
        return_value=httpx.Response(200, text="ok")
    )
    client = HttpClient(config(respect_robots=True))
    try:
        assert (await client.get("https://portal.test.invalid/offen/liste")).text == "ok"
    finally:
        await client.aclose()


@respx.mock
async def test_missing_robots_txt_allows_request():
    respx.get("https://portal.test.invalid/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://portal.test.invalid/feed.xml").mock(
        return_value=httpx.Response(200, text="feed")
    )
    client = HttpClient(config(respect_robots=True))
    try:
        assert (await client.get("https://portal.test.invalid/feed.xml")).text == "feed"
    finally:
        await client.aclose()


@respx.mock
async def test_cache_prevents_second_request(tmp_path: Path):
    route = respx.get("https://api.test.invalid/cached").mock(
        return_value=httpx.Response(200, json={"value": 1})
    )
    client = build_http_client(config(cache_enabled=True, cache_ttl_seconds=60), tmp_path / "cache")
    try:
        first = await client.get("https://api.test.invalid/cached")
        second = await client.get("https://api.test.invalid/cached")
        assert first.json() == second.json()
        assert route.call_count == 1
        assert client.stats.cache_hits == 1
    finally:
        await client.aclose()


async def test_rate_limiter_enforces_minimum_interval():
    import time

    limiter = RateLimiter(default_rps=50)  # 20 ms Abstand
    started = time.monotonic()
    await limiter.acquire("host")
    await limiter.acquire("host")
    assert time.monotonic() - started >= 0.015


def test_cache_expires(tmp_path: Path):
    cache = ResponseCache(tmp_path, ttl_seconds=0)
    key = ResponseCache.make_key("GET", "https://x.invalid")
    cache.set(key, status_code=200, content=b"data")
    assert cache.get(key) is None


# --- T-08: Crawl-delay nur verschaerfend, robots.txt ueber Request-Pfad ---------
@respx.mock
async def test_crawl_delay_only_tightens_rate_limit():
    respx.get("https://portal.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 1\n")
    )
    respx.get("https://portal.test.invalid/feed.xml").mock(
        return_value=httpx.Response(200, text="ok")
    )
    client = HttpClient(config(respect_robots=True))
    try:
        # Quelle ist strenger (0,5 req/s = 2 s) als Crawl-delay 1 s -> bleibt 2 s
        client.configure_host_rate("https://portal.test.invalid/", 0.5)
        await client.get("https://portal.test.invalid/feed.xml")
        assert client.rate_limiter.interval_for("portal.test.invalid") == 2.0
        # Quelle ist lockerer (2 req/s = 0,5 s) -> Crawl-delay verschaerft auf 1 s
        client.rate_limiter.configure_host("portal.test.invalid", 2.0)
        await client.get("https://portal.test.invalid/feed.xml")
        assert client.rate_limiter.interval_for("portal.test.invalid") == 1.0
    finally:
        await client.aclose()


@respx.mock
async def test_robots_fetch_goes_through_request_path():
    respx.get("https://portal.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    respx.get("https://portal.test.invalid/x").mock(return_value=httpx.Response(200, text="ok"))
    client = HttpClient(config(respect_robots=True))
    try:
        await client.get("https://portal.test.invalid/x")
        # robots.txt + eigentlicher Abruf werden beide gezaehlt
        assert client.stats.requests == 2
        assert client.stats.by_host["portal.test.invalid"] == 2
    finally:
        await client.aclose()


def test_tighten_host_never_loosens():
    limiter = RateLimiter(default_rps=1.0)
    limiter.configure_host("h", 0.5)  # 2 s
    limiter.tighten_host("h", 4.0)  # 0,25 s -> ignoriert
    assert limiter.interval_for("h") == 2.0
    limiter.tighten_host("h", 0.25)  # 4 s -> uebernommen
    assert limiter.interval_for("h") == 4.0
    limiter.tighten_host("h", None)
    assert limiter.interval_for("h") == 4.0


# --- T-18: Streaming-Download mit Groessenlimit ---------------------------------
@respx.mock
async def test_download_writes_file_and_counts_bytes(tmp_path: Path):
    respx.get("https://files.test.invalid/lv.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 " + b"x" * 1000)
    )
    client = HttpClient(config())
    try:
        target = tmp_path / "docs" / "lv.pdf"
        path = await client.download("https://files.test.invalid/lv.pdf", target)
        assert path == target
        assert target.read_bytes().startswith(b"%PDF-1.4")
        assert client.stats.bytes_downloaded == 1009
        assert list(tmp_path.glob("**/*.part")) == []
    finally:
        await client.aclose()


@respx.mock
async def test_download_aborts_above_limit_without_leaving_partial(tmp_path: Path):
    respx.get("https://files.test.invalid/gross.pdf").mock(
        return_value=httpx.Response(200, content=b"x" * 200_000)
    )
    client = HttpClient(config())
    try:
        target = tmp_path / "gross.pdf"
        with pytest.raises(HttpError, match="max_download_bytes"):
            await client.download("https://files.test.invalid/gross.pdf", target, max_bytes=50_000)
        assert not target.exists()
        assert list(tmp_path.glob("**/*.part")) == []
    finally:
        await client.aclose()


@respx.mock
async def test_download_checks_content_type(tmp_path: Path):
    respx.get("https://files.test.invalid/seite.html").mock(
        return_value=httpx.Response(200, content=b"<html>", headers={"Content-Type": "text/html"})
    )
    client = HttpClient(config())
    try:
        with pytest.raises(HttpError, match="Content-Type"):
            await client.download(
                "https://files.test.invalid/seite.html",
                tmp_path / "x.pdf",
                expected_types={"application/pdf"},
            )
        assert list(tmp_path.glob("**/*")) == []
    finally:
        await client.aclose()


@respx.mock
async def test_download_respects_robots(tmp_path: Path):
    respx.get("https://files.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /")
    )
    file_route = respx.get("https://files.test.invalid/lv.pdf")
    client = HttpClient(config(respect_robots=True))
    try:
        with pytest.raises(RobotsDisallowedError):
            await client.download("https://files.test.invalid/lv.pdf", tmp_path / "lv.pdf")
        assert file_route.call_count == 0
    finally:
        await client.aclose()


@respx.mock
async def test_download_error_status_leaves_no_file(tmp_path: Path):
    respx.get("https://files.test.invalid/fehlt.pdf").mock(return_value=httpx.Response(404))
    client = HttpClient(config())
    try:
        with pytest.raises(HttpError):
            await client.download("https://files.test.invalid/fehlt.pdf", tmp_path / "f.pdf")
        assert list(tmp_path.glob("**/*")) == []
    finally:
        await client.aclose()
