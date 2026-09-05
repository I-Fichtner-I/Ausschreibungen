"""Registry der Preisquellen.

Neue Preisquelle hinzufuegen:
1. Adapterklasse von ``PriceSource`` ableiten, ``type_name`` setzen
2. Klasse mit ``@register_price_source`` dekorieren
3. Modul in ``_ensure_loaded`` importieren
4. Eintrag in config.yaml unter ``price_sources:`` anlegen
"""

from __future__ import annotations

from collections.abc import Iterable

from ...config import Settings
from ...core.http import HttpClient
from ...core.logging import get_logger
from .base import PriceSource

log = get_logger(__name__)

PRICE_SOURCE_TYPES: dict[str, type[PriceSource]] = {}
_loaded = False


def register_price_source(cls: type[PriceSource]) -> type[PriceSource]:
    PRICE_SOURCE_TYPES[cls.type_name] = cls
    return cls


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    from . import catalog  # noqa: F401

    _loaded = True


def available_price_source_types() -> list[str]:
    _ensure_loaded()
    return sorted(PRICE_SOURCE_TYPES)


def build_price_sources(
    settings: Settings,
    http: HttpClient,
    only: Iterable[str] | None = None,
    include_disabled: bool = False,
) -> list[PriceSource]:
    """Konfigurierte Preisquellen instanziieren, sortiert nach Prioritaet."""
    _ensure_loaded()
    wanted = {name.lower() for name in only} if only else None
    sources: list[PriceSource] = []

    for name, config in settings.price_sources.items():
        if wanted is not None and name.lower() not in wanted:
            continue
        if not config.enabled and not include_disabled and wanted is None:
            continue
        cls = PRICE_SOURCE_TYPES.get(config.type)
        if cls is None:
            log.error(
                "unknown_price_source_type",
                source=name,
                type=config.type,
                known=sorted(PRICE_SOURCE_TYPES),
            )
            continue
        try:
            sources.append(cls(name=name, config=config, http=http, settings=settings))
        except Exception as exc:  # noqa: BLE001 - eine kaputte Quelle stoppt den Lauf nicht
            log.error("price_source_init_failed", source=name, error=str(exc))
            continue

    if wanted:
        for missing in sorted(wanted - {source.name.lower() for source in sources}):
            log.error("price_source_not_configured", source=missing)
    sources.sort(key=lambda source: source.priority)
    return sources
