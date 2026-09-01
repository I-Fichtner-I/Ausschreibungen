"""Registry der Quell-Adapter.

Neue Quelle hinzufuegen:
1. Adapterklasse von ``TenderSource`` ableiten, ``type_name`` setzen
2. Klasse mit ``@register_source`` dekorieren
3. Modul in ``_ensure_loaded`` bzw. ``sources/__init__.py`` importieren
4. Eintrag in config.yaml unter ``sources:`` anlegen
"""

from __future__ import annotations

from collections.abc import Iterable

from ..config import Settings
from ..core.http import HttpClient
from ..core.logging import get_logger
from .base import TenderSource

log = get_logger(__name__)

SOURCE_TYPES: dict[str, type[TenderSource]] = {}
_loaded = False


def register_source(cls: type[TenderSource]) -> type[TenderSource]:
    SOURCE_TYPES[cls.type_name] = cls
    return cls


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    # Import registriert die eingebauten Adapter ueber den Dekorator.
    from . import fixture, rss, ted  # noqa: F401

    _loaded = True


def available_types() -> list[str]:
    _ensure_loaded()
    return sorted(SOURCE_TYPES)


def build_sources(
    settings: Settings,
    http: HttpClient,
    only: Iterable[str] | None = None,
    include_disabled: bool = False,
) -> list[TenderSource]:
    """Konfigurierte Quellen instanziieren, sortiert nach Prioritaet."""
    _ensure_loaded()
    wanted = {name.lower() for name in only} if only else None
    sources: list[TenderSource] = []

    for name, config in settings.sources.items():
        if wanted is not None and name.lower() not in wanted:
            continue
        if not config.enabled and not include_disabled and wanted is None:
            continue
        cls = SOURCE_TYPES.get(config.type)
        if cls is None:
            log.error(
                "unknown_source_type",
                source=name,
                type=config.type,
                known=sorted(SOURCE_TYPES),
            )
            continue
        try:
            source = cls(name=name, config=config, http=http, settings=settings)
        except Exception as exc:  # noqa: BLE001 - eine kaputte Quelle darf den Lauf nicht stoppen
            log.error("source_init_failed", source=name, error=str(exc))
            continue
        sources.append(source)

    if wanted:
        missing = wanted - {s.name.lower() for s in sources}
        for name in sorted(missing):
            log.error("source_not_configured", source=name)

    return sorted(sources, key=lambda s: s.priority)
