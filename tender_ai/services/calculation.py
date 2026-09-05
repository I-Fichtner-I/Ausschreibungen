"""Stufe 5: Kalkulieren, bewerten, zur Freigabe vorlegen.

Setzt auf Stufe 4 auf: aus den gespeicherten Angeboten entstehen Kosten,
Angebotspreis, Marge und ein Urteil gegen die Mindestkriterien.

Der Dienst gibt **nichts ab**. Er erzeugt eine Entscheidungsvorlage; jeder
verbindliche Schritt bleibt beim Menschen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..calculation import build_scenario, evaluate_criteria, position_cost, review_notes
from ..calculation.scoring import score_calculation
from ..config import Settings
from ..core.errors import ConfigError
from ..core.logging import get_logger
from ..database.repository import TenderRepository
from ..database.session import session_scope
from ..models.calculation import ScenarioKind, TenderCalculation, Verdict
from ..models.price import ItemPricing, PriceQuote, PriceStatistics, ProductMatch

log = get_logger(__name__)


def _pricing_from_db(repository: TenderRepository, tender_id: str) -> list[ItemPricing]:
    """Gespeicherte Angebote zurueck in das Preismodell wandeln."""
    quotes_by_item: dict[int, list[Any]] = {}
    for record in repository.quotes_for(tender_id):
        quotes_by_item.setdefault(record.item_id, []).append(record)

    items: list[ItemPricing] = []
    for item_record in repository.items_for(tender_id):
        item = TenderRepository.to_item(item_record)
        pricing = ItemPricing(
            position=item.position, title=item.title, quantity=item.quantity, unit=item.unit
        )
        amounts: list[float] = []
        for record in quotes_by_item.get(item_record.id, []):
            quote = PriceQuote(
                supplier=record.supplier,
                product_name=record.product_name,
                manufacturer=record.manufacturer,
                model_number=record.model_number,
                article_number=record.article_number,
                amount=record.amount,
                currency=record.currency,
                basis=record.basis,
                vat_rate=record.vat_rate,
                unit=record.unit,
                shipping_cost=record.shipping_cost,
                shipping_included=record.shipping_included,
                min_order_quantity=record.min_order_quantity,
                availability=record.availability,
                lead_time_days=record.lead_time_days,
                url=record.url,
                retrieved_at=record.retrieved_at,
                warnings=list(record.warnings or []),
            )
            pricing.matches.append(
                ProductMatch(
                    quote=quote,
                    match_confidence=record.match_confidence,
                    reasons=list(record.reasons or []),
                    concerns=list(record.concerns or []),
                )
            )
            if record.net_amount is not None:
                amounts.append(record.net_amount)

        # Die Statistik wird aus den gespeicherten Nettobetraegen rekonstruiert;
        # nur so tragen die Szenarien dieselben Zahlen wie die Recherche.
        usable = sorted(
            record.net_amount
            for record in quotes_by_item.get(item_record.id, [])
            if record.net_amount is not None and record.match_confidence >= 0
        )
        if usable:
            from statistics import median

            currency = next(
                (
                    record.currency
                    for record in quotes_by_item.get(item_record.id, [])
                    if record.currency
                ),
                None,
            )
            pricing.statistics = PriceStatistics(
                offer_count=len(quotes_by_item.get(item_record.id, [])),
                usable_count=len(usable),
                currency=currency,
                minimum=usable[0],
                maximum=usable[-1],
                median=float(median(usable)),
            )
        else:
            pricing.statistics = PriceStatistics(
                offer_count=len(quotes_by_item.get(item_record.id, []))
            )
        items.append(pricing)
    return items


def calculate_tender(settings: Settings, tender_id: str) -> TenderCalculation:
    """Kosten, Marge und Urteil einer Ausschreibung berechnen."""
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(tender_id)
        if record is None:
            raise ConfigError(f"Ausschreibung nicht gefunden: {tender_id}")
        resolved_id = record.id
        tender = TenderRepository.to_tender(record)
        risk = repository.risk_for(resolved_id)
        pricing_items = _pricing_from_db(repository, resolved_id)

        calculation = TenderCalculation(
            tender_id=resolved_id,
            risk_score=risk.score if risk else None,
            tender_estimated_value=tender.estimated_value,
        )
        threshold = settings.criteria.minimum_match_confidence

        if not pricing_items:
            calculation.warnings.append(
                "Keine Positionen mit Preisen vorhanden - zuerst 'tender-ai items' "
                "und 'tender-ai prices' ausfuehren."
            )
            calculation.review_notes = review_notes(calculation)
            repository.save_calculation(calculation, record)
            session.commit()
            return calculation

        calculation.positions = [
            position_cost(item, config=settings.calculation, minimum_confidence=threshold)
            for item in pricing_items
        ]
        calculation.currency = next(
            (item.statistics.currency for item in pricing_items if item.statistics.currency),
            None,
        )
        calculation.coverage_percent = round(
            calculation.calculated_count * 100 / len(calculation.positions)
        )
        # Der Angebotspreis entsteht einmal - aus dem Erwartungsfall. Die
        # anderen Faelle rechnen gegen genau diesen Preis: geboten wird einmal,
        # eingekauft wird spaeter, und das ist das Risiko, das hier sichtbar
        # werden soll.
        expected = build_scenario(
            ScenarioKind.EXPECTED,
            pricing_items,
            config=settings.calculation,
            minimum_confidence=threshold,
            currency=calculation.currency,
        )
        calculation.scenarios = (
            [
                build_scenario(
                    kind,
                    pricing_items,
                    config=settings.calculation,
                    minimum_confidence=threshold,
                    currency=calculation.currency,
                    fixed_sale_total=expected.sale_total,
                )
                for kind in (ScenarioKind.BEST,)
            ]
            + [expected]
            + [
                build_scenario(
                    ScenarioKind.WORST,
                    pricing_items,
                    config=settings.calculation,
                    minimum_confidence=threshold,
                    currency=calculation.currency,
                    fixed_sale_total=expected.sale_total,
                )
            ]
        )
        calculation.criteria = evaluate_criteria(
            calculation,
            calculation.expected,
            criteria=settings.criteria,
            days_until_deadline=tender.days_until_deadline,
        )
        calculation.score, calculation.verdict = score_calculation(
            calculation,
            calculation.expected,
            scoring=settings.scoring,
            calculation_config=settings.calculation,
        )
        if calculation.verdict is Verdict.NOT_ASSESSABLE:
            calculation.warnings.append(
                f"Abdeckung {calculation.coverage_percent} Prozent liegt unter der "
                f"geforderten {settings.calculation.minimum_coverage_percent} Prozent - "
                f"die Datenlage traegt keine Bewertung."
            )
        calculation.review_notes = review_notes(calculation)

        repository.save_calculation(calculation, record)
        session.commit()
        log.info(
            "tender_calculated",
            tender=resolved_id,
            verdict=str(calculation.verdict),
            score=calculation.score,
            coverage=calculation.coverage_percent,
        )
        return calculation


@dataclass(slots=True)
class BatchCalculationReport:
    """Ergebnis eines Stapellaufs - die Vorlage fuer den taeglichen Blick."""

    calculated: list[TenderCalculation] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.calculated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenders": self.count,
            "failed": self.failed,
            "results": [
                {
                    "tender_id": calculation.tender_id,
                    "verdict": str(calculation.verdict),
                    "score": calculation.score,
                    "coverage_percent": calculation.coverage_percent,
                    "margin_percent": (
                        calculation.expected.margin_percent if calculation.expected else None
                    ),
                }
                for calculation in sorted(
                    self.calculated, key=lambda item: item.score or -1, reverse=True
                )
            ],
            "is_binding_offer": False,
            "requires_user_approval": True,
        }


def calculate_open_tenders(settings: Settings, *, limit: int = 50) -> BatchCalculationReport:
    """Alle laufenden Ausschreibungen mit Preisen kalkulieren und priorisieren."""
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        candidates = [
            record.id
            for record in repository.list_tenders(limit=limit, open_only=True, order_by="deadline")
            if record.price_research is not None
        ]

    report = BatchCalculationReport()
    for tender_id in candidates:
        try:
            report.calculated.append(calculate_tender(settings, tender_id))
        except Exception as exc:  # noqa: BLE001 - eine Ausschreibung stoppt nie den Stapel
            log.error("calculation_failed", tender=tender_id, error=str(exc))
            report.failed.append({"tender_id": tender_id, "error": str(exc)})
    log.info("batch_calculation_done", tenders=report.count, failed=len(report.failed))
    return report


async def calculate_tender_async(settings: Settings, tender_id: str) -> TenderCalculation:
    """Asynchroner Einstieg - die Rechnung selbst braucht kein Netzwerk."""
    return await asyncio.to_thread(calculate_tender, settings, tender_id)
