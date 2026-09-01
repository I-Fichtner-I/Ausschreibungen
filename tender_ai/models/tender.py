"""Einheitliches Ausschreibungs-Datenmodell.

Alle Quellen liefern ihre Treffer in genau dieser Form. Quellenspezifische
Rohdaten bleiben unter ``raw`` erhalten, damit spaetere Stufen (Analyse,
Artikelextraktion) nichts verlieren und jede Aussage auf die Originalquelle
zurueckgefuehrt werden kann.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import Provenance, normalize_text, utcnow


class TenderStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    AWARDED = "AWARDED"
    CANCELLED = "CANCELLED"
    AMENDED = "AMENDED"
    UNKNOWN = "UNKNOWN"


class DocumentAccess(StrEnum):
    PUBLIC = "PUBLIC"  # frei abrufbar
    REGISTRATION = "REGISTRATION"  # Login/Registrierung noetig -> nicht automatisiert
    RESTRICTED = "RESTRICTED"  # sonstige Schranke
    UNKNOWN = "UNKNOWN"


class TenderDocument(BaseModel):
    name: str | None = None
    url: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    local_path: str | None = None
    access: DocumentAccess = DocumentAccess.UNKNOWN
    retrieved_at: datetime | None = None
    checksum_sha256: str | None = None
    note: str | None = None


class TenderLot(BaseModel):
    lot_id: str | None = None
    title: str | None = None
    description: str | None = None
    cpv_codes: list[str] = Field(default_factory=list)
    estimated_value: float | None = None
    currency: str | None = None
    delivery_location: str | None = None
    quantity_hint: str | None = None


class TenderRequirements(BaseModel):
    """Anforderungen aus der Bekanntmachung.

    In Stufe 1 (Recherche) meist leer - Bekanntmachungen enthalten diese
    Angaben nur teilweise. Stufe 2 (Dokumentenanalyse) fuellt die Felder aus
    den Vergabeunterlagen, jeweils mit Provenance.
    """

    eligibility: list[str] = Field(default_factory=list)
    technical: list[str] = Field(default_factory=list)
    minimum: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    delivery_terms: str | None = None
    payment_terms: str | None = None
    award_criteria: list[str] = Field(default_factory=list)
    price_weight_percent: float | None = None
    quality_weight_percent: float | None = None


class Tender(BaseModel):
    """Standardisierte Ausschreibung."""

    model_config = ConfigDict(use_enum_values=False)

    # --- Identitaet / Herkunft ---
    id: str = Field(description="Eindeutige ID: '<source>:<source_id>'")
    source: str
    source_id: str
    source_url: str | None = None
    national_id: str | None = Field(
        default=None, description="Amtliche Bekanntmachungs-/Vergabenummer, falls vorhanden"
    )

    # --- Kerndaten ---
    title: str | None = None
    contracting_authority: str | None = None
    authority_id: str | None = None
    description: str | None = None
    category: str | None = None
    cpv_codes: list[str] = Field(default_factory=list)
    country: str | None = None
    region: str | None = None
    delivery_location: str | None = None

    procedure_type: str | None = None
    notice_type: str | None = None
    status: TenderStatus = TenderStatus.UNKNOWN

    # --- Fristen ---
    publication_date: date | None = None
    submission_deadline: datetime | None = None
    binding_period_end: date | None = None
    delivery_deadline: date | None = None
    contract_duration: str | None = None

    # --- Wirtschaft ---
    estimated_value: float | None = None
    currency: str | None = None
    budget: float | None = None
    value_is_estimated: bool = False

    # --- Struktur / Details ---
    lots: list[TenderLot] = Field(default_factory=list)
    documents: list[TenderDocument] = Field(default_factory=list)
    requirements: TenderRequirements = Field(default_factory=TenderRequirements)

    # --- Metadaten ---
    language: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    provenance: Provenance | None = None
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)
    notes: list[str] = Field(default_factory=list)

    @field_validator("cpv_codes", mode="before")
    @classmethod
    def _clean_cpv(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(code).strip() for code in value if str(code).strip()]

    @field_validator("submission_deadline")
    @classmethod
    def _ensure_tz(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    # --- abgeleitete Werte ---
    @property
    def days_until_deadline(self) -> int | None:
        if self.submission_deadline is None:
            return None
        delta = self.submission_deadline - datetime.now(UTC)
        return delta.days

    @property
    def is_expired(self) -> bool:
        days = self.days_until_deadline
        return days is not None and days < 0

    def fingerprint(self) -> str:
        """Quellenuebergreifender Fingerabdruck fuer die Dublettenerkennung.

        Bevorzugt die amtliche Vergabenummer; sonst Titel + Vergabestelle +
        Abgabefrist. Der Wert ist absichtlich grob - die endgueltige
        Entscheidung trifft ``tender_ai.pipeline.dedup``.
        """
        if self.national_id:
            return hashlib.sha256(f"nid:{normalize_text(self.national_id)}".encode()).hexdigest()
        parts = [
            normalize_text(self.title),
            normalize_text(self.contracting_authority),
            self.submission_deadline.date().isoformat() if self.submission_deadline else "",
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def content_hash(self) -> str:
        """Hash der inhaltlich relevanten Felder - Basis der Aenderungserkennung."""
        payload = "|".join(
            [
                normalize_text(self.title),
                normalize_text(self.description)[:2000],
                str(self.submission_deadline),
                str(self.estimated_value),
                str(self.status),
                ",".join(sorted(self.cpv_codes)),
                str(len(self.lots)),
                ",".join(sorted(doc.url or doc.name or "" for doc in self.documents)),
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def summary_line(self) -> str:
        from .common import display

        return (
            f"{display(self.title)} | {display(self.contracting_authority)} | "
            f"Frist: {display(self.submission_deadline)} | Quelle: {self.source}"
        )


def make_tender_id(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"
