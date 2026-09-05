"""Datenmodell der Kalkulation und Bewertung (Stufe 5).

Hier entsteht die Zahl, auf die am Ende jemand schaut: die Marge. Damit sie
nicht mehr behauptet, als die Datenlage hergibt, gelten drei Regeln:

1. **Eine unvollstaendige Preisbasis ergibt keine Marge.** Sind nur 40 Prozent
   der Positionen bepreist, ist die Marge dieser 40 Prozent nicht die Marge des
   Auftrags. Unterhalb der konfigurierten Abdeckung wird deshalb gar nicht
   bewertet, sondern die Luecke benannt.
2. **Der Angebotspreis ist eine Rechnung, keine Abgabe.** Das Ergebnis ist eine
   Entscheidungsvorlage - kein Angebot, keine Zusage, keine Abgabe. Der Ablauf
   bleibt: Analyse -> Freigabe durch den Nutzer -> Angebotsentwurf -> manuelle
   Pruefung -> manuelle Abgabe.
3. **Szenarien statt Scheingenauigkeit.** Gerechnet wird mit dem guenstigsten,
   dem mittleren und dem teuersten belastbaren Einkaufspreis aus Stufe 4 - alle
   drei aus echten Angeboten, keines geschaetzt.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import display, utcnow


class Verdict(StrEnum):
    """Einschaetzung einer Ausschreibung - immer nur ein Vorschlag."""

    VERY_INTERESTING = "VERY_INTERESTING"
    INTERESTING = "INTERESTING"
    REVIEW = "REVIEW"  # lohnt eine Pruefung von Hand
    RATHER_UNINTERESTING = "RATHER_UNINTERESTING"
    UNSUITABLE = "UNSUITABLE"  # ein Mindestkriterium ist verletzt
    NOT_ASSESSABLE = "NOT_ASSESSABLE"  # Datenlage traegt keine Bewertung


class ScenarioKind(StrEnum):
    BEST = "BEST"  # guenstigster belastbarer Einkaufspreis
    EXPECTED = "EXPECTED"  # Median
    WORST = "WORST"  # teuerster


class PositionCost(BaseModel):
    """Kosten und Erloes einer einzelnen Position."""

    model_config = ConfigDict(use_enum_values=False)

    position: str | None = None
    title: str
    quantity: float | None = None
    unit: str | None = None

    #: Netto-Einkaufspreis je Einheit aus dem besten belastbaren Angebot.
    unit_purchase_price: float | None = None
    supplier: str | None = None
    match_confidence: int | None = None

    purchase_total: float | None = None
    shipping_total: float = 0.0
    #: Zuschlag fuer Handling, Lagerung, Montage - aus der Konfiguration.
    surcharge_total: float = 0.0
    cost_total: float | None = None

    #: Angebotspreis dieser Position (Kosten plus Aufschlag).
    sale_total: float | None = None
    margin_absolute: float | None = None
    margin_percent: float | None = None

    #: Warum diese Position nicht kalkuliert werden konnte.
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_calculated(self) -> bool:
        return self.cost_total is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": display(self.position),
            "title": self.title,
            "quantity": self.quantity,
            "unit": display(self.unit),
            "unit_purchase_price": self.unit_purchase_price,
            "supplier": display(self.supplier),
            "match_confidence": self.match_confidence,
            "purchase_total": self.purchase_total,
            "shipping_total": self.shipping_total,
            "surcharge_total": self.surcharge_total,
            "cost_total": self.cost_total,
            "sale_total": self.sale_total,
            "margin_absolute": self.margin_absolute,
            "margin_percent": self.margin_percent,
            "warnings": self.warnings,
        }


class Scenario(BaseModel):
    """Ein Rechenfall ueber alle kalkulierten Positionen."""

    model_config = ConfigDict(use_enum_values=False)

    kind: ScenarioKind
    cost_total: float = 0.0
    sale_total: float = 0.0
    margin_absolute: float = 0.0
    margin_percent: float | None = None
    #: Kapitalbindung: Marge bezogen auf den Einsatz.
    roi_percent: float | None = None
    currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "cost_total": round(self.cost_total, 2),
            "sale_total": round(self.sale_total, 2),
            "margin_absolute": round(self.margin_absolute, 2),
            "margin_percent": (
                round(self.margin_percent, 1) if self.margin_percent is not None else None
            ),
            "roi_percent": round(self.roi_percent, 1) if self.roi_percent is not None else None,
            "currency": display(self.currency),
        }


class CriterionResult(BaseModel):
    """Ein Mindestkriterium und ob es erfuellt ist - mit den Zahlen dahinter."""

    code: str
    label: str
    required: str
    actual: str
    passed: bool
    #: True, wenn das Kriterium mangels Daten nicht geprueft werden konnte.
    undetermined: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "required": self.required,
            "actual": self.actual,
            "passed": self.passed,
            "undetermined": self.undetermined,
        }


class TenderCalculation(BaseModel):
    """Kalkulation und Entscheidungsvorlage einer Ausschreibung.

    Ausdruecklich **kein** Angebot: das Ergebnis ist eine Rechnung zur
    Vorlage. Verbindlich wird nichts ohne Freigabe und manuelle Abgabe.
    """

    model_config = ConfigDict(use_enum_values=False)

    tender_id: str
    positions: list[PositionCost] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    criteria: list[CriterionResult] = Field(default_factory=list)

    verdict: Verdict = Verdict.NOT_ASSESSABLE
    score: int | None = None
    #: Anteil der Positionen, die in die Rechnung eingehen konnten.
    coverage_percent: int = 0
    currency: str | None = None
    #: Geschaetzter Auftragswert der Vergabestelle, falls veroeffentlicht.
    tender_estimated_value: float | None = None
    risk_score: int | None = None

    warnings: list[str] = Field(default_factory=list)
    #: Was ein Mensch vor der Freigabe pruefen sollte.
    review_notes: list[str] = Field(default_factory=list)
    calculated_at: datetime = Field(default_factory=utcnow)

    @property
    def calculated_count(self) -> int:
        return sum(1 for position in self.positions if position.is_calculated)

    def scenario_by_name(self, name: str) -> Scenario | None:
        """Szenario ueber seinen Namen - fuer Auswertungen ohne Enum-Import."""
        for scenario in self.scenarios:
            if str(scenario.kind) == name:
                return scenario
        return None

    def scenario(self, kind: ScenarioKind) -> Scenario | None:
        for scenario in self.scenarios:
            if scenario.kind is kind:
                return scenario
        return None

    @property
    def expected(self) -> Scenario | None:
        return self.scenario(ScenarioKind.EXPECTED)

    @property
    def failed_criteria(self) -> list[CriterionResult]:
        return [criterion for criterion in self.criteria if not criterion.passed]

    def as_dict(self) -> dict[str, Any]:
        expected = self.expected
        return {
            "tender_id": self.tender_id,
            "verdict": str(self.verdict),
            "score": self.score,
            "coverage_percent": self.coverage_percent,
            "position_count": len(self.positions),
            "calculated_count": self.calculated_count,
            "currency": display(self.currency),
            "tender_estimated_value": self.tender_estimated_value,
            "risk_score": self.risk_score,
            "expected": expected.as_dict() if expected else None,
            "scenarios": [scenario.as_dict() for scenario in self.scenarios],
            "criteria": [criterion.as_dict() for criterion in self.criteria],
            "positions": [position.as_dict() for position in self.positions],
            "warnings": self.warnings,
            "review_notes": self.review_notes,
            "calculated_at": self.calculated_at.isoformat(),
            # Unmissverstaendlich in jeder maschinellen Ausgabe:
            "is_binding_offer": False,
            "requires_user_approval": True,
        }
