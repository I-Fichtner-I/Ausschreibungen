from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tender_ai.export.exporters import export_tenders, tender_rows
from tender_ai.models.tender import Tender


def tenders() -> list[Tender]:
    return [
        Tender(
            id="ted:1",
            source="ted",
            source_id="1",
            title="Lieferung von 2.000 Monitoren",
            contracting_authority="Musterstadt",
            submission_deadline=datetime(2036, 9, 15, 10, tzinfo=UTC),
            estimated_value=420000.0,
            currency="EUR",
        ),
        Tender(id="feed:2", source="feed", source_id="2", title="Ohne weitere Angaben"),
    ]


def test_rows_mark_missing_values_as_unknown():
    rows = tender_rows(tenders())
    assert rows[1]["contracting_authority"] == "UNKNOWN"
    assert rows[1]["estimated_value"] == "UNKNOWN"
    assert rows[0]["estimated_value"] == "420000.0"


def test_json_export(tmp_path: Path):
    path = export_tenders(tenders(), tmp_path / "out.json", "json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert payload["missing_value_marker"] == "UNKNOWN"
    assert payload["tenders"][0]["title"] == "Lieferung von 2.000 Monitoren"


def test_csv_export(tmp_path: Path):
    path = export_tenders(tenders(), tmp_path / "out.csv", "csv")
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert len(rows) == 2
    assert rows[0]["title"] == "Lieferung von 2.000 Monitoren"
    assert rows[1]["currency"] == "UNKNOWN"


def test_xlsx_export(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    path = export_tenders(tenders(), tmp_path / "out.xlsx", "xlsx")
    sheet = openpyxl.load_workbook(path).active
    assert sheet.max_row == 3  # Kopfzeile + 2 Datensaetze
    assert sheet.cell(row=1, column=1).value == "id"


def test_unknown_format_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        export_tenders(tenders(), tmp_path / "out.txt", "txt")
