"""HTML-Extraktion mit BeautifulSoup (Text und Tabellen)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from bs4 import BeautifulSoup

from ..models.document import ExtractedDocument, ExtractedPage, ExtractedTable
from .base import DocumentExtractor, register_extractor

#: Elemente, deren Inhalt kein Dokumenttext ist.
_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer")


@register_extractor
class HtmlExtractor(DocumentExtractor):
    name: ClassVar[str] = "html"
    media_types: ClassVar[tuple[str, ...]] = ("text/html", "application/xhtml+xml")
    suffixes: ClassVar[tuple[str, ...]] = (".html", ".htm")

    def _extract(self, path: Path, document: ExtractedDocument) -> None:
        markup = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(markup, "lxml")

        if soup.title and soup.title.string:
            document.metadata["title"] = soup.title.string.strip()

        for index, table in enumerate(soup.find_all("table")):
            extracted = _table_from_html(table, index)
            if extracted is not None:
                document.tables.append(extracted)

        for tag in soup(_NOISE_TAGS):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        document.pages.append(ExtractedPage(number=1, text=text))


def _table_from_html(table: object, index: int) -> ExtractedTable | None:
    rows: list[list[str]] = []
    header: list[str] | None = None
    for row in table.find_all("tr"):  # type: ignore[attr-defined]
        cells = row.find_all(["th", "td"])
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if not any(values):
            continue
        if header is None and cells and all(cell.name == "th" for cell in cells):
            header = values
            continue
        rows.append(values)
    if not rows and header is None:
        return None
    return ExtractedTable(page=1, index=index, header=header, rows=rows)
