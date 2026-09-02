"""Extraktor fuer reine Text- und CSV-Dateien."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import ClassVar

from ..models.document import ExtractedDocument, ExtractedPage, ExtractedTable
from .base import DocumentExtractor, register_extractor


@register_extractor
class PlainTextExtractor(DocumentExtractor):
    name: ClassVar[str] = "text"
    media_types: ClassVar[tuple[str, ...]] = ("text/plain", "text/csv", "application/csv")
    suffixes: ClassVar[tuple[str, ...]] = (".txt", ".csv", ".md")

    def _extract(self, path: Path, document: ExtractedDocument) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        document.pages.append(ExtractedPage(number=1, text=text.strip()))

        if path.suffix.lower() != ".csv":
            return
        # CSV zusaetzlich als Tabelle bereitstellen; das Trennzeichen wird
        # erraten, weil deutsche Exporte oft Semikolon verwenden.
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
        except csv.Error:
            delimiter = ";"
        rows = [row for row in csv.reader(text.splitlines(), delimiter=delimiter) if any(row)]
        if not rows:
            return
        header = rows[0] if len(rows) > 1 and all(cell.strip() for cell in rows[0]) else None
        document.tables.append(
            ExtractedTable(page=1, index=0, header=header, rows=rows[1:] if header else rows)
        )
