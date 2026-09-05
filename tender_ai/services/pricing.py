"""Stufe 4: Preise zu den erkannten Positionen recherchieren.

Setzt auf Stufe 3 auf: zu jeder Position des Leistungsverzeichnisses werden
die konfigurierten Preisquellen befragt, die Treffer begruendet zugeordnet und
zu einem Preisbild verdichtet.

Zwei Dinge macht der Dienst ausdruecklich **nicht**: Er rechnet keine Waehrung
um und leitet aus einem Bruttopreis ohne Steuersatz keinen Nettopreis ab. Beide
Abkuerzungen wuerden eine Zahl erzeugen, die es nicht gibt - und der Fehler
faellt erst in der Marge auf.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..core.errors import ConfigError
from ..core.http import build_http_client
from ..core.logging import get_logger
from ..database.repository import TenderRepository
from ..database.session import session_scope
from ..models.item import TenderItem
from ..models.price import ItemPricing, PriceQuote, PricingResult
from ..pricing.matching import match_quotes
from ..pricing.sources import ProductQuery, build_price_sources
from ..pricing.statistics import price_statistics

log = get_logger(__name__)


async def research_prices(
    settings: Settings,
    tender_id: str,
    *,
    only_sources: list[str] | None = None,
    minimum_confidence: int | None = None,
) -> PricingResult:
    """Preise fuer alle Positionen einer Ausschreibung recherchieren."""
    threshold = (
        minimum_confidence
        if minimum_confidence is not None
        else settings.criteria.minimum_match_confidence
    )
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(tender_id)
        if record is None:
            raise ConfigError(f"Ausschreibung nicht gefunden: {tender_id}")
        resolved_id = record.id
        items = [
            TenderRepository.to_item(item)
            for item in repository.items_for(resolved_id)[: settings.pricing.max_items]
        ]

    result = PricingResult(tender_id=resolved_id)
    if not items:
        result.warnings.append("Keine Positionen vorhanden - zuerst 'tender-ai items' ausfuehren.")
        return result

    http = build_http_client(settings.http, settings.cache_dir)
    try:
        sources = build_price_sources(settings, http, only=only_sources)
        if not sources:
            result.warnings.append(
                "Keine Preisquelle aktiv - in config.yaml unter 'price_sources' "
                "eine Lieferantenliste eintragen."
            )
            return result
        result.sources_used = [source.name for source in sources]

        for item in items:
            result.items.append(await _price_item(item, sources, settings, threshold, result))
        for source in sources:
            await source.aclose()
    finally:
        await http.aclose()

    if result.sources_failed:
        result.warnings.append(
            f"{len(result.sources_failed)} Preisquelle(n) gestoert - das Preisbild ist "
            f"unvollstaendig."
        )
    log.info(
        "prices_researched",
        tender=resolved_id,
        items=len(result.items),
        usable=result.usable_count,
        coverage=result.coverage_percent,
    )
    return result


async def _price_item(
    item: TenderItem,
    sources: list[Any],
    settings: Settings,
    threshold: int,
    result: PricingResult,
) -> ItemPricing:
    """Eine Position bepreisen; eine gestoerte Quelle stoppt nichts."""
    pricing = ItemPricing(
        position=item.position, title=item.title, quantity=item.quantity, unit=item.unit
    )
    query = ProductQuery.from_item(item, max_results=settings.pricing.max_offers_per_item)
    quotes: list[PriceQuote] = []

    for source in sources:
        try:
            quotes.extend(await source.search(query))
        except Exception as exc:  # noqa: BLE001 - eine Quelle stoppt nie die Recherche
            log.error("price_source_failed", source=source.name, error=str(exc))
            failure = {"source": source.name, "error": str(exc)}
            if failure not in result.sources_failed:
                result.sources_failed.append(failure)

    pricing.matches = match_quotes(item, quotes, limit=settings.pricing.max_offers_per_item)
    pricing.statistics, warnings = price_statistics(
        pricing.matches,
        minimum_confidence=threshold,
        quantity=item.quantity,
        currencies=settings.pricing.currencies,
    )
    pricing.warnings.extend(warnings)

    if not pricing.matches:
        pricing.warnings.append("Kein Angebot gefunden - Position bleibt ohne Preis.")
    elif pricing.statistics.usable_count == 0:
        best = pricing.best_match
        if best is not None and best.match_confidence < threshold:
            # Der konkrete Grund ersetzt den allgemeinen aus der Statistik -
            # zweimal dasselbe zu sagen macht die Ausgabe nur unleserlich.
            pricing.warnings = [
                warning
                for warning in pricing.warnings
                if not warning.startswith("Kein Angebot ist kalkulationsfaehig")
            ]
            pricing.warnings.append(
                f"Bestes Angebot erreicht nur {best.match_confidence} von {threshold} "
                f"Punkten Zuordnungsguete - zur Pruefung, nicht zur Kalkulation."
            )
    elif pricing.statistics.is_single_source:
        pricing.warnings.append(
            "Nur ein kalkulationsfaehiges Angebot - das ist ein Datenpunkt, kein Marktpreis."
        )
    spread = pricing.statistics.spread_ratio
    if spread is not None and spread > settings.pricing.spread_warning_ratio:
        pricing.warnings.append(
            f"Preise streuen um {round(spread * 100)} Prozent des Medians - "
            f"vermutlich ist ein unpassendes Produkt darunter."
        )
    return pricing


async def research_and_store(
    settings: Settings,
    tender_id: str,
    *,
    only_sources: list[str] | None = None,
    minimum_confidence: int | None = None,
) -> PricingResult:
    """Recherchieren und das Ergebnis speichern."""
    result = await research_prices(
        settings,
        tender_id,
        only_sources=only_sources,
        minimum_confidence=minimum_confidence,
    )
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(result.tender_id)
        repository.save_pricing(result, record)
        session.commit()
    return result


@dataclass(slots=True)
class BatchPricingReport:
    """Ergebnis eines Stapellaufs der Preisrecherche."""

    researched: list[PricingResult] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.researched)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenders": self.count,
            "failed": self.failed,
            "results": [
                {
                    "tender_id": result.tender_id,
                    "items": len(result.items),
                    "usable": result.usable_count,
                    "coverage_percent": result.coverage_percent,
                }
                for result in sorted(
                    self.researched, key=lambda item: item.coverage_percent, reverse=True
                )
            ],
        }


async def research_open_tenders(
    settings: Settings,
    *,
    limit: int = 50,
    skip_researched: bool = True,
) -> BatchPricingReport:
    """Preise fuer alle laufenden Ausschreibungen mit Positionen recherchieren."""
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        candidates: list[str] = []
        for record in repository.list_tenders(limit=limit, open_only=True, order_by="deadline"):
            if not record.items:
                continue  # ohne Positionen gibt es nichts zu bepreisen
            already_current = (
                skip_researched
                and record.price_research is not None
                and record.price_research.content_hash == record.content_hash
            )
            if already_current:
                continue
            candidates.append(record.id)

    report = BatchPricingReport()
    for tender_id in candidates:
        try:
            report.researched.append(await research_and_store(settings, tender_id))
        except Exception as exc:  # noqa: BLE001 - eine Ausschreibung stoppt nie den Stapel
            log.error("pricing_failed", tender=tender_id, error=str(exc))
            report.failed.append({"tender_id": tender_id, "error": str(exc)})
    log.info("batch_pricing_done", tenders=report.count, failed=len(report.failed))
    return report


def research_prices_sync(settings: Settings, tender_id: str, **kwargs: Any) -> PricingResult:
    """Synchroner Einstieg fuer CLI und Skripte."""
    return asyncio.run(research_and_store(settings, tender_id, **kwargs))
