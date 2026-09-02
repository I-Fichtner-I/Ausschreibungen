"""Datenmodelle der Ausschreibungsanalyse (Stufe 2B).

Zwei Regeln bestimmen den Zuschnitt:

1. **Jeder Fund ist belegbar.** Ein erkannter Hinweis traegt immer seinen
   Originaltext und die Fundstelle (Dokument, Seite) - sonst kann niemand
   nachvollziehen, warum eine Ausschreibung als riskant gilt.
2. **Nichts wird erfunden.** Was nicht in den Unterlagen steht, bleibt leer.
   Ein fehlender Hinweis ist keine Entwarnung, sondern Unsicherheit - und die
   wird als eigener Risikofaktor ausgewiesen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance, utcnow


class RequirementKind(StrEnum):
    """Art eines erkannten Hinweises in den Vergabeunterlagen."""

    CERTIFICATION = "CERTIFICATION"  # Zertifikate, Normen, Nachweise
    ELIGIBILITY = "ELIGIBILITY"  # Eignung, Referenzen, Praequalifikation
    TECHNICAL = "TECHNICAL"  # technische Spezifikation
    MINIMUM = "MINIMUM"  # ausdrueckliche Mindestanforderung
    BRAND_LOCK = "BRAND_LOCK"  # Marken-/Herstellerbindung
    PAYMENT_TERMS = "PAYMENT_TERMS"  # Zahlungsziel, Skonto, Vorkasse
    DELIVERY_TERMS = "DELIVERY_TERMS"  # Lieferort, Incoterm, Anlieferung
    AWARD_CRITERIA = "AWARD_CRITERIA"  # Zuschlagskriterien, Gewichtung
    WARRANTY = "WARRANTY"  # Gewaehrleistung, Garantie
    PENALTY = "PENALTY"  # Vertragsstrafe, Poenale
    SECURITY = "SECURITY"  # Sicherheitsleistung, Buergschaft


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class RequirementFinding(BaseModel):
    """Ein einzelner Fund in den Unterlagen."""

    kind: RequirementKind
    #: Kurzform fuer Listen - der belastbare Beleg ist ``provenance.original_text``.
    text: str
    #: Normierter Wert, sofern ableitbar (z. B. "ISO 9001", "30" Tage Zahlungsziel).
    value: str | None = None
    #: 0-100. Regelbasierte Funde liegen bewusst nicht bei 100: ein Stichwort
    #: im Text ist ein Hinweis, keine Rechtsauskunft.
    confidence: int = 70
    provenance: Provenance | None = None

    def evidence(self) -> str:
        if self.provenance and self.provenance.original_text:
            return self.provenance.original_text
        return self.text


class RiskFactor(BaseModel):
    """Ein Beitrag zum Risiko-Score - mit Begruendung und Beleg."""

    code: str
    label: str
    points: int
    #: Warum dieser Faktor zaehlt, in einem Satz.
    explanation: str
    findings: list[RequirementFinding] = Field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
            "explanation": self.explanation,
            "evidence": [finding.evidence() for finding in self.findings][:3],
        }


class RiskAssessment(BaseModel):
    """Risikobewertung einer Ausschreibung (0 = unauffaellig, 100 = maximal)."""

    model_config = ConfigDict(use_enum_values=False)

    tender_id: str
    score: int = 0
    level: RiskLevel = RiskLevel.LOW
    factors: list[RiskFactor] = Field(default_factory=list)
    #: Worauf die Bewertung beruht - fehlende Unterlagen sind selbst ein Risiko.
    documents_analyzed: int = 0
    documents_unreadable: int = 0
    characters_analyzed: int = 0
    computed_at: datetime = Field(default_factory=utcnow)

    @property
    def top_factors(self) -> list[RiskFactor]:
        return sorted(self.factors, key=lambda factor: factor.points, reverse=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "score": self.score,
            "level": str(self.level),
            "documents_analyzed": self.documents_analyzed,
            "documents_unreadable": self.documents_unreadable,
            "characters_analyzed": self.characters_analyzed,
            "computed_at": self.computed_at.isoformat(),
            "factors": [factor.as_dict() for factor in self.top_factors],
        }


class AnalysisResult(BaseModel):
    """Ergebnis einer Dokumentenanalyse: Funde plus Risikobewertung."""

    tender_id: str
    findings: list[RequirementFinding] = Field(default_factory=list)
    risk: RiskAssessment

    def by_kind(self, kind: RequirementKind) -> list[RequirementFinding]:
        return [finding for finding in self.findings if finding.kind is kind]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "findings": [
                {
                    "kind": str(finding.kind),
                    "text": finding.text,
                    "value": finding.value,
                    "confidence": finding.confidence,
                    "document": finding.provenance.document if finding.provenance else None,
                    "page": finding.provenance.page if finding.provenance else None,
                    "original_text": finding.provenance.original_text
                    if finding.provenance
                    else None,
                }
                for finding in self.findings
            ],
            "risk": self.risk.as_dict(),
        }
