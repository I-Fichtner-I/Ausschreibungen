"""robots.txt-Pruefung.

Compliance-Regel des Projekts: Zugriffsbeschraenkungen werden respektiert und
nicht umgangen. Diese Klasse laedt robots.txt je Host einmal und cached das
Ergebnis fuer die Laufzeit des Prozesses.

Verhalten bei Unklarheit:
- robots.txt nicht vorhanden (404) -> Abruf erlaubt (Standardinterpretation)
- robots.txt nicht erreichbar (Netzwerk-/Serverfehler) -> Abruf erlaubt, aber
  mit Warnung; das Rate-Limit bleibt aktiv
- explizites Disallow -> Abruf wird verweigert (RobotsDisallowedError)
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .logging import get_logger

log = get_logger(__name__)


class RobotsGuard:
    def __init__(self, user_agent: str, enabled: bool = True) -> None:
        self.user_agent = user_agent
        self.enabled = enabled
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    async def _load(self, origin: str, client: httpx.AsyncClient) -> RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            response = await client.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=10.0,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            log.warning("robots_unreachable", origin=origin, error=str(exc))
            return None
        if response.status_code >= 400:
            # 404 = keine robots.txt -> alles erlaubt; 5xx -> keine Aussage moeglich
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    async def allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        if not self.enabled:
            return True
        origin = self._origin(url)
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(origin, client)
        parser = self._parsers[origin]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parsers.get(self._origin(url))
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.user_agent)
        except Exception:  # pragma: no cover - defensiv
            return None
        return float(delay) if delay is not None else None
