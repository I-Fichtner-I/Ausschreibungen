"""Datenmodell der Artikelerkennung (Stufe 3).

Aus den in Stufe 2 ausgelesenen Unterlagen werden die zu liefernden Positionen
gewonnen - die Grundlage jeder spaeteren Preisrecherche. Drei Regeln bestimmen
den Zuschnitt:

1. **Nichts wird erfunden.** Was in der Zeile nicht steht, bleibt ``None`` und
   erscheint in der Ausgabe als UNKNOWN. Eine Position ohne Menge ist eine
   Position ohne Menge - keine Position mit Menge 1.
2. **Jede Position ist belegbar.** Dokument, Seite, Abschnitt und die
   Originalzeile bleiben erhalten, damit jede Zahl im Leistungsverzeichnis
   nachgeschlagen werden kann.
3. **Erkennung und Zuordnung sind zwei Dinge.** ``confidence`` sagt, wie
   sicher die Zeile *gelesen* wurde; ``match_confidence`` bleibt leer, bis in
   Stufe 4 ein konkretes Produkt zugeordnet ist.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance, display, normalize_text, utcnow


class ItemSourceKind(StrEnum):
    """Woher die Position stammt - Tabellen sind deutlich verlaesslicher."""

    TABLE = "TABLE"  # Zeile einer erkannten Tabelle (Leistungsverzeichnis)
    TEXT = "TEXT"  # Positionsmuster im Fliesstext (Rueckfallebene)
    MANUAL = "MANUAL"  # von Hand ergaenzt


#: Obergrenze der Erkennungs-Konfidenz. Auch eine perfekt gefuellte Tabellen-
#: zeile bleibt eine Auslegung des Dokuments, keine bestaetigte Bestellung.
MAX_CONFIDENCE = 95


class TenderItem(BaseModel):
    """Eine Position aus dem Leistungsverzeichnis."""

    model_config = ConfigDict(use_enum_values=False)

    #: Ordnungszahl der Position ("1", "1.10", "02.03.0040").
    position: str | None = None
    #: Kurzbezeichnung - die erste Zeile der Positionsbeschreibung.
    title: str
    #: Vollstaendiger Positionstext, sofern laenger als der Titel.
    description: str | None = None

    quantity: float | None = None
    #: True, wenn die Menge nicht eindeutig gelesen werden konnte
    #: (z. B. "ca. 20") - dann ist sie eine Schaetzung, nie ein Fixwert.
    quantity_estimated: bool = False
    #: Normierte Mengeneinheit ("STK", "M", "KG", ...).
    unit: str | None = None
    #: Einheit so, wie sie im Dokument steht - fuer den Beleg.
    unit_original: str | None = None

    manufacturer: str | None = None
    model_number: str | None = None
    article_number: str | None = None
    #: Weitere erkannte Merkmale (Farbe, Norm, Abmessung, ...).
    specifications: dict[str, str] = Field(default_factory=dict)
    #: True, wenn Fabrikat/Typ ohne Gleichwertigkeitsklausel vorgegeben ist.
    brand_locked: bool = False

    #: 0-100: wie sicher die Zeile als Position gelesen wurde.
    confidence: int = 0
    #: 0-100: Guete der Produktzuordnung. Bleibt ``None`` bis Stufe 4 - eine
    #: unsichere Zuordnung wird nicht durch eine erfundene Zahl kaschiert.
    match_confidence: int | None = None

    source_kind: ItemSourceKind = ItemSourceKind.TABLE
    provenance: Provenance | None = None
    #: Was beim Lesen aufgefallen ist ("Menge nicht lesbar: 'auf Abruf'").
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_priceable(self) -> bool:
        """Reicht die Position fuer eine Preisrecherche (Stufe 4) aus?"""
        return bool(self.title.strip()) and self.quantity is not None and self.unit is not None

    @property
    def label(self) -> str:
        if self.position:
            return f"{self.position} {self.title}"
        return self.title

    def evidence(self) -> str:
        if self.provenance and self.provenance.original_text:
            return self.provenance.original_text
        return self.label

    def dedup_key(self) -> tuple[str, str, str]:
        """Schluessel zum Erkennen derselben Position in mehreren Dateien.

        Dasselbe Leistungsverzeichnis liegt oft als PDF *und* als Tabelle bei;
        beide beschreiben dieselbe Position.
        """
        return (
            (self.position or "").strip(),
            normalize_text(self.title)[:80],
            "" if self.quantity is None else f"{self.quantity:.3f}",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": display(self.position),
            "title": self.title,
            "description": self.description,
            "quantity": self.quantity,
            "quantity_estimated": self.quantity_estimated,
            "unit": display(self.unit),
            "unit_original": self.unit_original,
            "manufacturer": display(self.manufacturer),
            "model_number": display(self.model_number),
            "article_number": display(self.article_number),
            "specifications": self.specifications,
            "brand_locked": self.brand_locked,
            "confidence": self.confidence,
            "match_confidence": self.match_confidence,
            "source_kind": str(self.source_kind),
            "document": self.provenance.document if self.provenance else None,
            "page": self.provenance.page if self.provenance else None,
            "section": self.provenance.section if self.provenance else None,
            "evidence": self.evidence(),
            "warnings": self.warnings,
        }


class ItemExtractionResult(BaseModel):
    """Ergebnis der Artikelerkennung fuer eine Ausschreibung."""

    model_config = ConfigDict(use_enum_values=False)

    tender_id: str
    items: list[TenderItem] = Field(default_factory=list)
    #: Worauf die Erkennung beruht - fuer die Einordnung des Ergebnisses.
    documents_scanned: int = 0
    tables_scanned: int = 0
    tables_used: int = 0
    #: Hinweise auf Grenzen des Ergebnisses (keine Tabelle gefunden o. Ae.).
    warnings: list[str] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=utcnow)

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def priceable_count(self) -> int:
        return sum(1 for item in self.items if item.is_priceable)

    @property
    def average_confidence(self) -> int:
        if not self.items:
            return 0
        return round(sum(item.confidence for item in self.items) / len(self.items))

    @property
    def total_quantity_known(self) -> bool:
        """True, wenn zu jeder Position eine Menge vorliegt."""
        return bool(self.items) and all(item.quantity is not None for item in self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "item_count": self.item_count,
            "priceable_count": self.priceable_count,
            "average_confidence": self.average_confidence,
            "documents_scanned": self.documents_scanned,
            "tables_scanned": self.tables_scanned,
            "tables_used": self.tables_used,
            "warnings": self.warnings,
            "extracted_at": self.extracted_at.isoformat(),
            "items": [item.as_dict() for item in self.items],
        }
