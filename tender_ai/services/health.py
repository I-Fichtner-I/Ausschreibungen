"""Health-Checks der Quellen als wiederverwendbarer Dienst."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ..config import Settings
from ..core.http import build_http_client
from ..sources.base import SourceStatus
from ..sources.registry import build_sources


async def check_sources(
    settings: Settings, only: Sequence[str] | None = None
) -> list[SourceStatus]:
    """Jede konfigurierte Quelle mit einem Probeabruf pruefen.

    Auch deaktivierte Quellen werden geprueft - der Befehl soll zeigen, ob eine
    Quelle funktionieren *wuerde*. Eine leere Liste bedeutet: nichts
    konfiguriert oder nichts ausgewaehlt.
    """
    http = build_http_client(settings.http, settings.cache_dir)
    try:
        sources = build_sources(settings, http, only=only, include_disabled=True)
        if not sources:
            return []
        return list(await asyncio.gather(*(source.health_check() for source in sources)))
    finally:
        await http.aclose()
