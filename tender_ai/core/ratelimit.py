"""Rate-Limiting pro Host.

Bewusst konservativ: eine feste Mindestpause zwischen zwei Anfragen an
denselben Host. Damit bleibt das Tool auch bei vielen Quellen ein hoeflicher
Gast auf fremden Servern.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class RateLimiter:
    """Erzwingt ``1 / requests_per_second`` Sekunden Abstand je Host."""

    def __init__(self, default_rps: float = 1.0) -> None:
        self._default_interval = self._interval(default_rps)
        self._intervals: dict[str, float] = {}
        self._next_allowed: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def _interval(rps: float) -> float:
        return 1.0 / rps if rps and rps > 0 else 0.0

    def configure_host(self, host: str, requests_per_second: float | None) -> None:
        if requests_per_second is not None:
            self._intervals[host] = self._interval(requests_per_second)

    async def acquire(self, host: str) -> None:
        interval = self._intervals.get(host, self._default_interval)
        if interval <= 0:
            return
        async with self._locks[host]:
            now = time.monotonic()
            wait = self._next_allowed[host] - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed[host] = now + interval
