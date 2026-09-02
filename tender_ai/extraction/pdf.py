"""PDF-Extraktion mit pdfplumber (Text und Tabellen je Seite)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pdfplumber

from ..models.document import ExtractedDocument, ExtractedPage, ExtractedTable
from .base import DocumentExtractor, register_extractor


@register_extractor
class PdfExtractor(DocumentExtractor):
    name: ClassVar[str] = "pdf"
    media_types: ClassVar[tuple[str, ...]] = ("application/pdf",)
    suffixes: ClassVar[tuple[str, ...]] = (".pdf",)

    def _extract(self, path: Path, document: ExtractedDocument) -> None:
        with pdfplumber.open(path) as pdf:
            document.metadata = {
                key: str(value)
                for key, value in (pdf.metadata or {}).items()
                if value not in (None, "")
            }
            for number, page in enumerate(pdf.pages, start=1):
                document.pages.append(
                    ExtractedPage(number=number, text=(page.extract_text() or "").strip())
                )
                for index, table in enumerate(page.extract_tables() or []):
                    extracted = _to_table(table, page=number, index=index)
                    if extracted is not None:
                        document.tables.append(extracted)


def _to_table(raw: list[list[str | None]], *, page: int, index: int) -> ExtractedTable | None:
    """Rohtabelle saeubern; leere Tabellen werden verworfen."""
    rows = [
        [(cell or "").strip().replace("\n", " ") for cell in row]
        for row in raw
        if any((cell or "").strip() for cell in row)
    ]
    if not rows:
        return None
    header = rows[0] if _looks_like_header(rows) else None
    return ExtractedTable(page=page, index=index, header=header, rows=rows[1:] if header else rows)


def _looks_like_header(rows: list[list[str]]) -> bool:
    """Erste Zeile als Kopfzeile werten, wenn sie gefuellt und untypisch fuer Daten ist.

    Bewusst zurueckhaltend: lieber keine Kopfzeile annehmen, als eine Datenzeile
    zu verlieren - die Artikelerkennung in Stufe 3 arbeitet sonst auf falschen
    Spalten.
    """
    if len(rows) < 2:
        return False
    first = rows[0]
    if not all(cell.strip() for cell in first):
        return False
    # Kopfzeilen bestehen selten nur aus Zahlen.
    return not all(cell.replace(",", "").replace(".", "").isdigit() for cell in first)
