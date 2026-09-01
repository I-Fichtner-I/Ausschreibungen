"""Exportfunktionen.

Fehlende Werte erscheinen im Export als ``UNKNOWN`` - so ist auf einen Blick
erkennbar, dass eine Angabe in der Quelle fehlt und nicht etwa null betraegt.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ..models.common import UNKNOWN, display
from ..models.tender import Tender

EXPORT_FORMATS = ("json", "csv", "xlsx")

COLUMNS = (
    "id",
    "source",
    "source_id",
    "national_id",
    "title",
    "contracting_authority",
    "country",
    "region",
    "cpv_codes",
    "publication_date",
    "submission_deadline",
    "days_until_deadline",
    "estimated_value",
    "currency",
    "status",
    "procedure_type",
    "lots",
    "documents",
    "source_url",
    "retrieved_at",
)


def tender_rows(tenders: Iterable[Tender]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tender in tenders:
        rows.append(
            {
                "id": tender.id,
                "source": tender.source,
                "source_id": tender.source_id,
                "national_id": display(tender.national_id),
                "title": display(tender.title),
                "contracting_authority": display(tender.contracting_authority),
                "country": display(tender.country),
                "region": display(tender.region),
                "cpv_codes": display(tender.cpv_codes),
                "publication_date": display(tender.publication_date),
                "submission_deadline": display(tender.submission_deadline),
                "days_until_deadline": display(tender.days_until_deadline),
                "estimated_value": display(tender.estimated_value),
                "currency": display(tender.currency),
                "status": str(tender.status),
                "procedure_type": display(tender.procedure_type),
                "lots": len(tender.lots) if tender.lots else 0,
                "documents": len(tender.documents) if tender.documents else 0,
                "source_url": display(tender.source_url),
                "retrieved_at": display(tender.retrieved_at),
            }
        )
    return rows


def export_tenders(tenders: Sequence[Tender], destination: Path, fmt: str = "json") -> Path:
    fmt = fmt.lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unbekanntes Format '{fmt}'. Erlaubt: {', '.join(EXPORT_FORMATS)}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        payload = {
            "count": len(tenders),
            "missing_value_marker": UNKNOWN,
            "tenders": [tender.model_dump(mode="json") for tender in tenders],
        }
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    rows = tender_rows(tenders)
    if fmt == "csv":
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return destination

    # xlsx
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - optionale Abhaengigkeit
        raise RuntimeError("Excel-Export benoetigt openpyxl: pip install openpyxl") from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ausschreibungen"
    sheet.append(list(COLUMNS))
    for row in rows:
        sheet.append([row.get(column, UNKNOWN) for column in COLUMNS])
    for index, column in enumerate(COLUMNS, start=1):
        width = max(
            12, min(60, max((len(str(row.get(column, ""))) for row in rows), default=12) + 2)
        )
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"
    workbook.save(destination)
    return destination
