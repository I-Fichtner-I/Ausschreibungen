"""Datenmodell der Preisrecherche (Stufe 4).

Ein Preis ist keine Zahl. Ohne Quelle, Zeitpunkt, Waehrung, Netto/Brutto-Status,
Versandkosten und Verfuegbarkeit ist er fuer eine Kalkulation wertlos - und
schlimmer als wertlos, wenn er trotzdem verwendet wird. Deshalb traegt jedes
Angebot diese Angaben mit, und was fehlt, bleibt ``None`` statt geraten zu
werden.

Zwei Regeln bestimmen den Zuschnitt:

1. **Netto und Brutto werden nie stillschweigend ineinander umgerechnet.**
   Ohne bekannten Steuersatz gibt es keinen Nettopreis - dann liefert
   ``net_amount()`` nichts und nennt den Grund.
2. **Eine unsichere Produktzuordnung wird nicht zur sicheren gemacht.**
   ``match_confidence`` traegt immer die Begruendungen mit, aus denen sie
   entstanden ist; unterhalb der konfigurierten Schwelle ist ein Angebot nicht
   kalkulationsfaehig, sondern ein Vorschlag zur Pruefung.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance, display, utcnow


class PriceBasis(StrEnum):
    """Bezugsgroesse eines Preises - der haeufigste Kalkulationsfehler."""

    NET = "NET"  # ohne Umsatzsteuer
    GROSS = "GROSS"  # inklusive Umsatzsteuer
    UNKNOWN = "UNKNOWN"  # nicht ausgewiesen - nicht raten


class Availability(StrEnum):
    IN_STOCK = "IN_STOCK"
    ON_ORDER = "ON_ORDER"  # bestellbar, mit Lieferzeit
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


#: Obergrenze der Zuordnungsguete. Auch eine identische Artikelnummer bleibt
#: eine Zuordnung aus zwei Textquellen, keine bestaetigte Bestellung.
MAX_MATCH_CONFIDENCE = 95


class PriceTier(BaseModel):
    """Staffelpreis: ab dieser Menge gilt dieser Preis."""

    min_quantity: float
    amount: float

    def applies_to(self, quantity: float | None) -> bool:
        return quantity is not None and quantity >= self.min_quantity


class PriceQuote(BaseModel):
    """Ein Preis fuer ein Produkt bei einem Lieferanten, zu einem Zeitpunkt."""

    model_config = ConfigDict(use_enum_values=False)

    supplier: str
    product_name: str
    manufacturer: str | None = None
    model_number: str | None = None
    article_number: str | None = None

    amount: float | None = None
    currency: str | None = None
    basis: PriceBasis = PriceBasis.UNKNOWN
    #: Steuersatz als Anteil (0.19), falls ausgewiesen. Ohne ihn ist aus einem
    #: Bruttopreis kein Nettopreis abzuleiten.
    vat_rate: float | None = None
    #: Einheit, auf die sich der Preis bezieht ("STK", "M", ...).
    unit: str | None = None
    #: Staffelpreise, aufsteigend nach Mindestmenge.
    tiers: list[PriceTier] = Field(default_factory=list)

    shipping_cost: float | None = None
    shipping_included: bool | None = None
    min_order_quantity: float | None = None
    packaging_unit: float | None = None

    availability: Availability = Availability.UNKNOWN
    lead_time_days: int | None = None

    url: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    provenance: Provenance | None = None
    #: Was beim Lesen unklar blieb ("Netto/Brutto nicht ausgewiesen").
    warnings: list[str] = Field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.amount is not None and self.currency is not None

    def amount_for(self, quantity: float | None) -> float | None:
        """Preis fuer eine Menge - beruecksichtigt Staffeln.

        Ohne Menge gilt der Grundpreis: eine Staffel zu unterstellen, waere
        eine Annahme ueber eine Bestellung, die es noch nicht gibt.
        """
        if quantity is None:
            return self.amount
        applicable = [tier for tier in self.tiers if tier.applies_to(quantity)]
        if applicable:
            return min(applicable, key=lambda tier: tier.amount).amount
        return self.amount

    def net_amount(self, quantity: float | None = None) -> tuple[float | None, str | None]:
        """(Nettopreis, Grund fuer das Fehlen).

        Aus einem Bruttopreis ohne ausgewiesenen Steuersatz wird hier **kein**
        Nettopreis: 19 Prozent zu unterstellen ist bei ermaessigten Saetzen,
        Auslandslieferungen oder Reverse-Charge schlicht falsch - und der
        Fehler faellt erst in der Marge auf.
        """
        amount = self.amount_for(quantity)
        if amount is None:
            return None, "Kein Preis vorhanden"
        if self.basis is PriceBasis.NET:
            return amount, None
        if self.basis is PriceBasis.GROSS:
            if self.vat_rate is None:
                return None, "Bruttopreis ohne ausgewiesenen Steuersatz"
            return amount / (1.0 + self.vat_rate), None
        return None, "Netto/Brutto nicht ausgewiesen"

    def as_dict(self) -> dict[str, Any]:
        net, net_reason = self.net_amount()
        return {
            "supplier": self.supplier,
            "product_name": self.product_name,
            "manufacturer": display(self.manufacturer),
            "model_number": display(self.model_number),
            "article_number": display(self.article_number),
            "amount": self.amount,
            "currency": display(self.currency),
            "basis": str(self.basis),
            "vat_rate": self.vat_rate,
            "net_amount": net,
            "net_unavailable_because": net_reason,
            "unit": display(self.unit),
            "tiers": [{"min_quantity": t.min_quantity, "amount": t.amount} for t in self.tiers],
            "shipping_cost": self.shipping_cost,
            "shipping_included": self.shipping_included,
            "min_order_quantity": self.min_order_quantity,
            "availability": str(self.availability),
            "lead_time_days": self.lead_time_days,
            "url": self.url,
            "retrieved_at": self.retrieved_at.isoformat(),
            "warnings": self.warnings,
        }


class ProductMatch(BaseModel):
    """Ein Angebot, einer Position zugeordnet - mit Begruendung."""

    model_config = ConfigDict(use_enum_values=False)

    quote: PriceQuote
    #: 0-100. Nie 100: die Zuordnung entsteht aus zwei Textquellen.
    match_confidence: int = 0
    #: Warum diese Guete - jede Zeile ein nachvollziehbarer Grund.
    reasons: list[str] = Field(default_factory=list)
    #: Warum die Zuordnung zweifelhaft ist (abweichendes Fabrikat, Einheit).
    concerns: list[str] = Field(default_factory=list)

    def is_usable(self, minimum_confidence: int) -> bool:
        """Reicht die Zuordnung fuer eine Kalkulation?

        Unterhalb der Schwelle ist das Angebot ein Vorschlag zur Pruefung -
        nicht die Grundlage einer Marge.
        """
        return self.match_confidence >= minimum_confidence and self.quote.has_price

    def as_dict(self) -> dict[str, Any]:
        return {
            "match_confidence": self.match_confidence,
            "reasons": self.reasons,
            "concerns": self.concerns,
            "quote": self.quote.as_dict(),
        }


class PriceStatistics(BaseModel):
    """Preisbild einer Position ueber alle brauchbaren Angebote."""

    offer_count: int = 0
    usable_count: int = 0
    currency: str | None = None
    minimum: float | None = None
    median: float | None = None
    maximum: float | None = None
    #: Streuung als Anteil des Medians - ein Warnsignal fuer falsche Zuordnung.
    spread_ratio: float | None = None

    @property
    def is_single_source(self) -> bool:
        """Ein einziges Angebot ist kein Marktpreis, sondern ein Datenpunkt."""
        return self.usable_count == 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_count": self.offer_count,
            "usable_count": self.usable_count,
            "currency": display(self.currency),
            "minimum": self.minimum,
            "median": self.median,
            "maximum": self.maximum,
            "spread_ratio": self.spread_ratio,
            "single_source": self.is_single_source,
        }


class ItemPricing(BaseModel):
    """Preisrecherche zu genau einer Position des Leistungsverzeichnisses."""

    model_config = ConfigDict(use_enum_values=False)

    position: str | None = None
    title: str
    quantity: float | None = None
    unit: str | None = None
    matches: list[ProductMatch] = Field(default_factory=list)
    statistics: PriceStatistics = Field(default_factory=PriceStatistics)
    #: Warum hier nichts (Brauchbares) gefunden wurde.
    warnings: list[str] = Field(default_factory=list)

    @property
    def best_match(self) -> ProductMatch | None:
        """Bestes Angebot nach Zuordnungsguete, dann Preis."""
        with_price = [match for match in self.matches if match.quote.has_price]
        if not with_price:
            return None
        return max(
            with_price,
            key=lambda match: (match.match_confidence, -(match.quote.amount or 0.0)),
        )

    def as_dict(self) -> dict[str, Any]:
        best = self.best_match
        return {
            "position": display(self.position),
            "title": self.title,
            "quantity": self.quantity,
            "unit": display(self.unit),
            "statistics": self.statistics.as_dict(),
            "best_match": best.as_dict() if best else None,
            "matches": [match.as_dict() for match in self.matches],
            "warnings": self.warnings,
        }


class PricingResult(BaseModel):
    """Ergebnis der Preisrecherche fuer eine Ausschreibung."""

    model_config = ConfigDict(use_enum_values=False)

    tender_id: str
    items: list[ItemPricing] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    sources_failed: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    researched_at: datetime = Field(default_factory=utcnow)

    @property
    def priced_count(self) -> int:
        return sum(1 for item in self.items if item.best_match is not None)

    @property
    def usable_count(self) -> int:
        return sum(1 for item in self.items if item.statistics.usable_count > 0)

    @property
    def coverage_percent(self) -> int:
        """Anteil der Positionen mit kalkulationsfaehigem Preis."""
        if not self.items:
            return 0
        return round(self.usable_count * 100 / len(self.items))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "item_count": len(self.items),
            "priced_count": self.priced_count,
            "usable_count": self.usable_count,
            "coverage_percent": self.coverage_percent,
            "sources_used": self.sources_used,
            "sources_failed": self.sources_failed,
            "warnings": self.warnings,
            "researched_at": self.researched_at.isoformat(),
            "items": [item.as_dict() for item in self.items],
        }
