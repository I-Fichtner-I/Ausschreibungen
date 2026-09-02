"""Datenmodell fuer extrahierte Dokumentinhalte.

Stufe 2 liest Vergabeunterlagen (PDF, DOCX, XLSX, HTML) aus. Damit spaetere
Stufen jede Aussage belegen koennen, bleibt bei jedem Textstueck erhalten,
aus welchem Dokument und welcher Seite es stammt - das ist die Grundlage der
Provenance-Anforderung (Dokument, Seite, Abschnitt, Originaltext).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import utcnow


class ExtractionStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"  # z. B. Textlimit erreicht oder einzelne Seiten unlesbar
    EMPTY = "EMPTY"  # Datei gelesen, aber kein Text gefunden (Scan ohne OCR?)
    UNSUPPORTED = "UNSUPPORTED"  # kein Extraktor fuer diesen Typ
    FAILED = "FAILED"  # Datei defekt oder nicht lesbar


class ExtractedTable(BaseModel):
    """Eine erkannte Tabelle - Rohform fuer die Artikelerkennung in Stufe 3."""

    page: int | None = None
    index: int = 0
    #: Erste Zeile, falls sie wie eine Kopfzeile aussieht.
    header: list[str] | None = None
    rows: list[list[str]] = Field(default_factory=list)
    #: Name des Arbeitsblatts (XLSX) bzw. Abschnitts, falls bekannt.
    section: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)


class ExtractedPage(BaseModel):
    number: int
    text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class ExtractedDocument(BaseModel):
    """Ergebnis einer Dokumentextraktion."""

    model_config = ConfigDict(use_enum_values=False)

    source_path: str
    file_name: str | None = None
    media_type: str | None = None
    extractor: str | None = None
    status: ExtractionStatus = ExtractionStatus.OK
    error: str | None = None

    pages: list[ExtractedPage] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    size_bytes: int | None = None
    checksum_sha256: str | None = None
    extracted_at: datetime = Field(default_factory=utcnow)
    #: True, wenn der Text am konfigurierten Limit abgeschnitten wurde.
    truncated: bool = False
    #: True, sobald OCR eingesetzt wurde (Stufe 2, gescannte Dokumente).
    ocr_used: bool = False

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        """Gesamttext, Seiten durch Leerzeilen getrennt."""
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    @property
    def character_count(self) -> int:
        return sum(len(page.text) for page in self.pages)

    def page_text(self, number: int) -> str | None:
        for page in self.pages:
            if page.number == number:
                return page.text
        return None

    def summary(self) -> str:
        return (
            f"{self.file_name or self.source_path}: {self.page_count} Seite(n), "
            f"{len(self.tables)} Tabelle(n), {self.character_count} Zeichen "
            f"[{self.status}]"
        )
