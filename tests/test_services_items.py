"""Stufe 3: Artikel-Service, Persistenz und CLI-Befehl."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from openpyxl import Workbook
from typer.testing import CliRunner

from tender_ai.cli import app
from tender_ai.config import Settings
from tender_ai.core.errors import ConfigError
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.services import extract_items_for_open_tenders, extract_tender_items, run_search
from tender_ai.sources.base import SearchQuery

runner = CliRunner()
XLSX_URL = "https://files.test.invalid/lv.xlsx"

LV_ROWS = (
    ("Pos.", "Bezeichnung", "Menge", "ME", "Hersteller", "Typ"),
    ("1.10", "Monitor 27 Zoll, Farbe: schwarz", 20, "Stk", "Muster GmbH", "MX-27"),
    ("1.20", "Tastatur kabelgebunden", 20, "Stk", "", ""),
    ("1.30", "Dockingstation", "ca. 15", "Stk", "", ""),
    ("1.40", "Vor-Ort-Service", "auf Abruf", "Std", "", ""),
    ("", "Zwischensumme", "", "", "", ""),
)


@pytest.fixture
def xlsx_bytes(tmp_path: Path) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "LV"
    for row in LV_ROWS:
        sheet.append(list(row))
    path = tmp_path / "lv.xlsx"
    workbook.save(path)
    return path.read_bytes()


@pytest.fixture
def prepared(settings: Settings, sample_tender_file: Path) -> Settings:
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "it-1",
                        "title": "Lieferung von Bildschirmarbeitsplaetzen",
                        "contracting_authority": "Musterstadt",
                        "country": "DEU",
                        "status": "OPEN",
                        "submission_deadline": "2036-10-15T12:00:00+02:00",
                        "documents": [
                            {
                                "name": "Leistungsverzeichnis",
                                "url": XLSX_URL,
                                "media_type": (
                                    "application/vnd.openxmlformats-officedocument."
                                    "spreadsheetml.sheet"
                                ),
                                "access": "PUBLIC",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return settings


def _mock_document(xlsx_bytes: bytes) -> None:
    respx.get(XLSX_URL).mock(
        return_value=httpx.Response(
            200,
            content=xlsx_bytes,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            },
        )
    )


@respx.mock
async def test_items_are_extracted_from_the_price_sheet(prepared: Settings, xlsx_bytes: bytes):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)

    result = await extract_tender_items(prepared, "fixture:it-1")

    assert [item.position for item in result.items] == ["1.10", "1.20", "1.30", "1.40"]
    monitor = result.items[0]
    assert monitor.quantity == 20.0
    assert monitor.unit == "STK"
    assert monitor.manufacturer == "Muster GmbH"
    assert monitor.model_number == "MX-27"
    assert monitor.brand_locked is True
    assert monitor.specifications == {"Farbe": "schwarz"}
    # Menge auf Abruf bleibt unbekannt statt erfunden
    assert result.items[3].quantity is None
    assert result.items[2].quantity_estimated is True
    assert result.priceable_count == 3


@respx.mock
async def test_extraction_is_persisted_and_reloadable(prepared: Settings, xlsx_bytes: bytes):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)

    result = await extract_tender_items(prepared, "fixture:it-1")

    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        records = repository.items_for("fixture:it-1")
        assert len(records) == result.item_count
        assert [record.ordinal for record in records] == list(range(len(records)))

        summary = repository.item_extraction_for("fixture:it-1")
        assert summary is not None
        assert summary.item_count == result.item_count
        assert summary.priceable_count == result.priceable_count
        assert summary.content_hash == repository.get("fixture:it-1").content_hash

        restored = TenderRepository.to_item(records[0])
        assert restored.title == result.items[0].title
        assert restored.unit == "STK"
        assert restored.provenance and restored.provenance.document
        assert restored.match_confidence is None  # Produktzuordnung erst in Stufe 4


@respx.mock
async def test_rerun_replaces_previous_items(prepared: Settings, xlsx_bytes: bytes):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)

    first = await extract_tender_items(prepared, "fixture:it-1")
    second = await extract_tender_items(prepared, "fixture:it-1", fetch_missing=False)

    assert first.item_count == second.item_count
    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        assert len(repository.items_for("fixture:it-1")) == second.item_count


@respx.mock
async def test_min_confidence_filter_in_repository(prepared: Settings, xlsx_bytes: bytes):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)
    await extract_tender_items(prepared, "fixture:it-1")

    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        assert repository.items_for("fixture:it-1", min_confidence=101) == []
        assert repository.items_for("fixture:it-1", min_confidence=1)


async def test_unknown_tender_is_reported(prepared: Settings):
    with pytest.raises(ConfigError):
        await extract_tender_items(prepared, "fixture:gibt-es-nicht")


@respx.mock
async def test_batch_skips_unchanged_tenders(prepared: Settings, xlsx_bytes: bytes):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)

    first = await extract_items_for_open_tenders(prepared, limit=10)
    assert first.count == 1
    assert first.item_count == 4

    second = await extract_items_for_open_tenders(prepared, limit=10)
    assert second.count == 0  # nichts hat sich geaendert

    third = await extract_items_for_open_tenders(prepared, limit=10, skip_extracted=False)
    assert third.count == 1


@respx.mock
async def test_batch_survives_a_failing_tender(
    prepared: Settings, xlsx_bytes: bytes, monkeypatch: pytest.MonkeyPatch
):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    _mock_document(xlsx_bytes)

    from tender_ai.services import items as items_service

    async def boom(*args, **kwargs):
        raise RuntimeError("Datei defekt")

    monkeypatch.setattr(items_service, "extract_tender_items", boom)
    report = await items_service.extract_items_for_open_tenders(prepared, limit=10)
    assert report.count == 0
    assert report.failed and report.failed[0]["error"] == "Datei defekt"


@respx.mock
def test_cli_items_lists_positions(prepared: Settings, xlsx_bytes: bytes):
    import asyncio

    asyncio.run(run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"]))
    _mock_document(xlsx_bytes)

    result = runner.invoke(
        app, ["items", "fixture:it-1", "--config", str(prepared.config_file), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["item_count"] == 4
    assert payload["priceable_count"] == 3
    positions = [item["position"] for item in payload["items"]]
    assert positions == ["1.10", "1.20", "1.30", "1.40"]
    assert payload["items"][3]["unit"] == "H"
    assert payload["items"][3]["quantity"] is None


@respx.mock
def test_cli_items_filters_by_confidence(prepared: Settings, xlsx_bytes: bytes):
    import asyncio

    asyncio.run(run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"]))
    _mock_document(xlsx_bytes)

    result = runner.invoke(
        app,
        [
            "items",
            "fixture:it-1",
            "--config",
            str(prepared.config_file),
            "--min-confidence",
            "101",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["items"] == []


def test_cli_items_requires_a_tender(settings: Settings):
    result = runner.invoke(app, ["items", "--config", str(settings.config_file)])
    assert result.exit_code == 1
    assert "Tender-ID" in result.output
