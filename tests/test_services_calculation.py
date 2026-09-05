"""Stufe 5: Kalkulations-Service, Persistenz und CLI-Befehl."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from tender_ai.cli import app
from tender_ai.config import Settings, load_settings
from tender_ai.core.errors import ConfigError
from tender_ai.database.models import TenderDocumentRecord
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.models.calculation import ScenarioKind, Verdict
from tender_ai.models.document import ExtractedDocument, ExtractedTable
from tender_ai.services import (
    calculate_open_tenders,
    calculate_tender,
    extract_tender_items,
    research_and_store,
    run_search,
)
from tender_ai.sources.base import SearchQuery

runner = CliRunner()

PRICE_LIST = (
    "Artikelnummer;Bezeichnung;Hersteller;Typ;Preis;Waehrung;Preisbasis;MwSt;Einheit;Lieferant\n"
    "MX27;Monitor 27 Zoll IPS;Muster GmbH;MX-27;180,00;EUR;netto;19;STK;Lieferant A\n"
    "MX27B;Monitor 27 Zoll IPS;Muster GmbH;MX-27;200,00;EUR;netto;19;STK;Lieferant B\n"
    "DS47;Dockingstation USB-C;Docking AG;DS-4711;90,00;EUR;netto;19;STK;Lieferant A\n"
)

LV_TABLE = ExtractedTable(
    page=1,
    header=["Pos.", "Bezeichnung", "Menge", "ME"],
    rows=[
        ["1.10", "Monitor 27 Zoll, Fabrikat: Muster GmbH, Typ: MX-27", "100", "Stk"],
        ["1.20", "Dockingstation USB-C, Fabrikat: Docking AG, Typ: DS-4711", "100", "Stk"],
    ],
)
#: Zweite Position ohne Entsprechung im Katalog - senkt die Abdeckung.
LV_TABLE_WITH_GAP = ExtractedTable(
    page=1,
    header=["Pos.", "Bezeichnung", "Menge", "ME"],
    rows=[
        *LV_TABLE.rows,
        ["1.30", "Sonderanfertigung ohne Katalogentsprechung", "10", "Stk"],
        ["1.40", "Weitere Sonderanfertigung", "10", "Stk"],
    ],
)


def _configure(settings: Settings, tmp_path: Path, **calculation) -> Settings:
    price_list = tmp_path / "preise.csv"
    price_list.write_text(PRICE_LIST, encoding="utf-8")
    config = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    config["price_sources"] = {"liste": {"type": "catalog", "path": str(price_list)}}
    config["calculation"] = {
        "markup_percent": 25.0,
        "overhead_percent": 0.0,
        "include_shipping": False,
        "minimum_coverage_percent": 80,
        **calculation,
    }
    settings.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return load_settings(settings.config_file)


@pytest.fixture
def prepared(settings: Settings, sample_tender_file: Path, tmp_path: Path) -> Settings:
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "ca-1",
                        "title": "Lieferung von Bildschirmarbeitsplaetzen",
                        "contracting_authority": "Musterstadt",
                        "country": "DEU",
                        "status": "OPEN",
                        "submission_deadline": "2036-10-15T12:00:00+02:00",
                        "estimated_value": 30000.0,
                        "currency": "EUR",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return _configure(settings, tmp_path)


async def _prepare_tender(settings: Settings, table: ExtractedTable = LV_TABLE) -> str:
    """Ausschreibung bis einschliesslich Preisrecherche vorbereiten."""
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    tender_id = "fixture:ca-1"
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        document = TenderDocumentRecord(
            tender_id=tender_id, name="LV.pdf", access="PUBLIC", media_type="application/pdf"
        )
        session.add(document)
        session.flush()
        repository.save_extract(
            document,
            ExtractedDocument(source_path="LV.pdf", file_name="LV.pdf", tables=[table]),
        )
        session.commit()
    await extract_tender_items(settings, tender_id, fetch_missing=False)
    await research_and_store(settings, tender_id)
    return tender_id


async def test_full_chain_produces_a_calculation(prepared: Settings):
    tender_id = await _prepare_tender(prepared)

    result = calculate_tender(prepared, tender_id)

    assert result.coverage_percent == 100
    assert result.calculated_count == 2
    assert result.currency == "EUR"
    expected = result.expected
    assert expected is not None
    # 100 x 180 (Median von 180/200 ist 200 -> siehe Statistik) + 100 x 90
    assert expected.cost_total > 0
    assert expected.sale_total == pytest.approx(expected.cost_total * 1.25)
    assert expected.margin_percent == pytest.approx(20.0)


async def test_scenarios_hold_the_offer_price_and_move_the_margin(prepared: Settings):
    tender_id = await _prepare_tender(prepared)

    result = calculate_tender(prepared, tender_id)

    best = result.scenario(ScenarioKind.BEST)
    expected = result.expected
    worst = result.scenario(ScenarioKind.WORST)
    assert best and expected and worst
    assert best.sale_total == pytest.approx(expected.sale_total)
    assert worst.sale_total == pytest.approx(expected.sale_total)
    assert best.cost_total < worst.cost_total
    assert best.margin_percent > worst.margin_percent


async def test_thin_coverage_blocks_the_verdict(prepared: Settings):
    """Zwei von vier Positionen bepreist - das traegt keine Bewertung."""
    tender_id = await _prepare_tender(prepared, LV_TABLE_WITH_GAP)

    result = calculate_tender(prepared, tender_id)

    assert result.coverage_percent == 50
    assert result.verdict is Verdict.NOT_ASSESSABLE
    assert result.score is None
    assert any("Abdeckung" in warning for warning in result.warnings)
    assert any("ohne belastbaren Preis" in note for note in result.review_notes)


async def test_lowering_the_coverage_requirement_allows_a_verdict(
    settings: Settings, sample_tender_file: Path, tmp_path: Path
):
    """Die Schwelle ist eine Entscheidung des Nutzers."""
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "ca-1",
                        "title": "Lieferung",
                        "status": "OPEN",
                        "submission_deadline": "2036-10-15T12:00:00+02:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lenient = _configure(settings, tmp_path, minimum_coverage_percent=40)
    tender_id = await _prepare_tender(lenient, LV_TABLE_WITH_GAP)

    result = calculate_tender(lenient, tender_id)

    assert result.coverage_percent == 50
    assert result.verdict is not Verdict.NOT_ASSESSABLE
    assert result.score is not None


async def test_result_is_persisted(prepared: Settings):
    tender_id = await _prepare_tender(prepared)

    result = calculate_tender(prepared, tender_id)

    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        record = repository.calculation_for(tender_id)
        assert record is not None
        assert record.verdict == str(result.verdict)
        assert record.coverage_percent == 100
        assert record.margin_percent == pytest.approx(20.0)
        assert record.scenarios and len(record.scenarios) == 3
        assert record.criteria
        assert record.review_notes
        assert record.content_hash == repository.get(tender_id).content_hash


async def test_calculation_without_prices_is_reported(prepared: Settings):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])

    result = calculate_tender(prepared, "fixture:ca-1")

    assert result.positions == []
    assert result.verdict is Verdict.NOT_ASSESSABLE
    assert any("zuerst" in warning for warning in result.warnings)


async def test_unknown_tender_is_reported(prepared: Settings):
    with pytest.raises(ConfigError):
        calculate_tender(prepared, "fixture:gibt-es-nicht")


async def test_batch_only_covers_tenders_with_prices(prepared: Settings):
    report = calculate_open_tenders(prepared, limit=10)
    assert report.count == 0  # noch keine Preisrecherche gelaufen

    await _prepare_tender(prepared)
    report = calculate_open_tenders(prepared, limit=10)
    assert report.count == 1
    assert report.as_dict()["is_binding_offer"] is False


async def test_cli_calculate_shows_the_decision_basis(prepared: Settings):
    tender_id = await _prepare_tender(prepared)

    result = runner.invoke(
        app, ["calculate", tender_id, "--config", str(prepared.config_file), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["coverage_percent"] == 100
    assert payload["expected"]["margin_percent"] == 20.0
    assert len(payload["scenarios"]) == 3
    assert payload["criteria"]
    # In jeder maschinellen Ausgabe unmissverstaendlich:
    assert payload["is_binding_offer"] is False
    assert payload["requires_user_approval"] is True


async def test_cli_calculate_names_it_no_offer(prepared: Settings):
    tender_id = await _prepare_tender(prepared)

    result = runner.invoke(app, ["calculate", tender_id, "--config", str(prepared.config_file)])

    assert result.exit_code == 0, result.output
    assert "kein Angebot" in result.output
    assert "Freigabe" in result.output


def test_cli_calculate_requires_a_tender(settings: Settings):
    result = runner.invoke(app, ["calculate", "--config", str(settings.config_file)])
    assert result.exit_code == 1
    assert "Tender-ID" in result.output
