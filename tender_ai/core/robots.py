"""robots.txt-Pruefung.

Compliance-Regel des Projekts: Zugriffsbeschraenkungen werden respektiert und
nicht umgangen. Diese Klasse laedt robots.txt je Host einmal und cached das
Ergebnis fuer die Laufzeit des Prozesses.

Der Abruf selbst laeuft ueber den regulaeren Request-Pfad des HttpClient
(``fetch``-Callable), damit Rate-Limit, Retry und Cache auch fuer robots.txt
gelten.

Verhalten bei Unklarheit:
- robots.txt nicht vorhanden (404) -> Abruf erlaubt (Standardinterpretation)
- robots.txt nicht erreichbar (Netzwerk-/Serverfehler) -> Abruf erlaubt, aber
  mit Warnung; das Rate-Limit bleibt aktiv
- explizites Disallow -> Abruf wird verweigert (RobotsDisallowedError)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .logging import get_logger

log = get_logger(__name__)

#: Liefert die robots.txt-Antwort oder ``None``, wenn sie nicht abrufbar ist.
RobotsFetcher = Callable[[str], Awaitable[httpx.Response | None]]


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

    async def _load(self, origin: str, fetch: RobotsFetcher) -> RobotFileParser | None:
        response = await fetch(f"{origin}/robots.txt")
        if response is None or response.status_code >= 400:
            # keine robots.txt bzw. keine Aussage moeglich -> alles erlaubt
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    async def allowed(self, url: str, fetch: RobotsFetcher) -> bool:
        if not self.enabled:
            return True
        origin = self._origin(url)
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(origin, fetch)
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
        except Exception:  # noqa: BLE001 - fehlerhafte robots.txt darf den Abruf nicht verhindern
            return None
        try:
            return float(delay) if delay is not None else None
        except (TypeError, ValueError):
            return None
