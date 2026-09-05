"""Stufe 4: Preis-Service, Persistenz und CLI-Befehl."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from tender_ai.cli import app
from tender_ai.config import Settings, load_settings
from tender_ai.core.errors import ConfigError
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.models.document import ExtractedDocument, ExtractedTable
from tender_ai.services import extract_tender_items, research_and_store, run_search
from tender_ai.services.pricing import research_open_tenders, research_prices
from tender_ai.sources.base import SearchQuery

runner = CliRunner()

PRICE_LIST = (
    "Artikelnummer;Bezeichnung;Hersteller;Typ;Preis;Waehrung;Preisbasis;MwSt;Einheit;"
    "Staffelpreise;Lieferant\n"
    "MX27-001;Monitor 27 Zoll IPS entspiegelt;Muster GmbH;MX-27;189,00;EUR;netto;19;STK;"
    "50:179,00|100:172,50;Muster Distribution\n"
    "MON27-B;Monitor 27 Zoll IPS;Andere AG;ZZ-1;119,00;EUR;netto;19;STK;;Zweitlieferant\n"
    "STU-9;Buerostuhl drehbar mit Armlehne;Sitzwerk;BS-9;129,00;EUR;netto;19;STK;;"
    "Muster Distribution\n"
)

LV_TABLE = ExtractedTable(
    page=1,
    header=["Pos.", "Bezeichnung", "Menge", "ME"],
    rows=[
        ["1.10", "Monitor 27 Zoll, Fabrikat: Muster GmbH, Typ: MX-27", "120", "Stk"],
        ["1.20", "Buerostuhl drehbar mit Armlehne", "40", "Stk"],
        ["1.30", "Spezialanfertigung ohne Entsprechung im Katalog", "5", "Stk"],
    ],
)


@pytest.fixture
def prepared(settings: Settings, sample_tender_file: Path, tmp_path: Path) -> Settings:
    """Ausschreibung mit Positionen plus aktive Preisliste."""
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "pr-1",
                        "title": "Lieferung von Bildschirmarbeitsplaetzen",
                        "contracting_authority": "Musterstadt",
                        "country": "DEU",
                        "status": "OPEN",
                        "submission_deadline": "2036-10-15T12:00:00+02:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    price_list = tmp_path / "preise.csv"
    price_list.write_text(PRICE_LIST, encoding="utf-8")

    config = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    config["price_sources"] = {
        "liste": {"type": "catalog", "path": str(price_list), "priority": 10}
    }
    settings.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return load_settings(settings.config_file)


async def _with_items(settings: Settings) -> str:
    """Ausschreibung anlegen, Extrakt einspielen, Positionen erkennen."""
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    tender_id = "fixture:pr-1"
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        from tender_ai.database.models import TenderDocumentRecord

        document = TenderDocumentRecord(
            tender_id=tender_id, name="LV.pdf", access="PUBLIC", media_type="application/pdf"
        )
        session.add(document)
        session.flush()
        repository.save_extract(
            document,
            ExtractedDocument(source_path="LV.pdf", file_name="LV.pdf", tables=[LV_TABLE]),
        )
        session.commit()
    await extract_tender_items(settings, tender_id, fetch_missing=False)
    return tender_id


async def test_prices_are_researched_for_every_item(prepared: Settings):
    tender_id = await _with_items(prepared)

    result = await research_prices(prepared, tender_id)

    assert len(result.items) == 3
    assert result.sources_used == ["liste"]
    monitor = result.items[0]
    assert monitor.position == "1.10"
    best = monitor.best_match
    assert best is not None
    assert best.quote.manufacturer == "Muster GmbH"
    assert best.match_confidence >= 85
    # Menge 120 loest die 100er-Staffel aus
    assert best.quote.net_amount(monitor.quantity)[0] == pytest.approx(172.5)


async def test_brand_locked_item_does_not_take_the_cheaper_make(prepared: Settings):
    """Der Kern der Stufe: 119 EUR statt 189 EUR waeren eine falsche Marge."""
    tender_id = await _with_items(prepared)

    monitor = (await research_prices(prepared, tender_id)).items[0]

    assert monitor.best_match is not None
    assert monitor.best_match.quote.supplier == "Muster Distribution"
    foreign = [m for m in monitor.matches if m.quote.manufacturer == "Andere AG"]
    assert foreign and not foreign[0].is_usable(prepared.criteria.minimum_match_confidence)
    assert any("Fabrikatsvorgabe" in concern for concern in foreign[0].concerns)


async def test_item_without_a_match_stays_without_a_price(prepared: Settings):
    tender_id = await _with_items(prepared)

    special = (await research_prices(prepared, tender_id)).items[2]

    assert special.best_match is None
    assert special.statistics.usable_count == 0
    assert any("Kein Angebot gefunden" in warning for warning in special.warnings)


async def test_name_only_match_is_a_proposal_not_a_basis(prepared: Settings):
    """Der Stuhl passt nur ueber den Namen - das traegt keine Kalkulation.

    Denselben "Buerostuhl drehbar mit Armlehne" verkaufen mehrere Hersteller;
    ohne Fabrikat oder Typ bleibt die Zuordnung ein Vorschlag zur Pruefung.
    """
    tender_id = await _with_items(prepared)
    chair = (await research_prices(prepared, tender_id)).items[1]

    assert chair.best_match is not None
    assert chair.best_match.quote.supplier == "Muster Distribution"
    assert chair.best_match.match_confidence < prepared.criteria.minimum_match_confidence
    assert chair.statistics.usable_count == 0
    assert any("Zuordnungsguete" in warning for warning in chair.warnings)


async def test_lowering_the_threshold_makes_it_calculable(prepared: Settings):
    """Die Schwelle ist eine Entscheidung des Nutzers, keine des Tools."""
    tender_id = await _with_items(prepared)

    strict = await research_prices(prepared, tender_id)
    lenient = await research_prices(prepared, tender_id, minimum_confidence=70)

    assert strict.usable_count == 1
    assert lenient.usable_count == 2
    assert lenient.items[1].statistics.is_single_source


async def test_coverage_reflects_calculable_items(prepared: Settings):
    tender_id = await _with_items(prepared)
    result = await research_prices(prepared, tender_id)
    # Nur der Monitor traegt Fabrikat und Typ und damit eine belastbare Zuordnung.
    assert result.usable_count == 1
    assert result.coverage_percent == 33


async def test_result_is_persisted_with_provenance(prepared: Settings):
    tender_id = await _with_items(prepared)

    result = await research_and_store(prepared, tender_id)

    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        quotes = repository.quotes_for(tender_id)
        assert quotes
        first = quotes[0]
        assert first.supplier == "Muster Distribution"
        assert first.basis == "NET"
        assert first.net_amount == pytest.approx(172.5)
        assert first.document == "preise.csv"
        assert first.reasons

        research = repository.price_research_for(tender_id)
        assert research is not None
        assert research.coverage_percent == result.coverage_percent
        assert research.content_hash == repository.get(tender_id).content_hash


async def test_rerun_replaces_previous_quotes(prepared: Settings):
    """Preise altern - alte und neue Angebote duerfen sich nicht mischen."""
    tender_id = await _with_items(prepared)
    await research_and_store(prepared, tender_id)
    await research_and_store(prepared, tender_id)

    with session_scope(prepared.database_url) as session:
        repository = TenderRepository(session, prepared.dedup)
        quotes = repository.quotes_for(tender_id)
        assert len({(q.item_id, q.rank) for q in quotes}) == len(quotes)


async def test_missing_items_are_reported(prepared: Settings):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    result = await research_prices(prepared, "fixture:pr-1")
    assert result.items == []
    assert any("zuerst" in warning for warning in result.warnings)


async def test_unknown_tender_is_reported(prepared: Settings):
    with pytest.raises(ConfigError):
        await research_prices(prepared, "fixture:gibt-es-nicht")


async def test_missing_price_source_is_reported(settings: Settings, sample_tender_file: Path):
    """Ohne Preisquelle gibt es keine stillen Nullpreise, sondern einen Hinweis."""
    sample_tender_file.write_text(
        json.dumps({"tenders": [{"source_id": "pr-1", "title": "Test", "status": "OPEN"}]}),
        encoding="utf-8",
    )
    tender_id = await _with_items(settings)
    result = await research_prices(settings, tender_id)
    assert any("Keine Preisquelle aktiv" in warning for warning in result.warnings)


async def test_broken_source_does_not_stop_the_run(prepared: Settings, tmp_path: Path):
    config = yaml.safe_load(prepared.config_file.read_text(encoding="utf-8"))
    config["price_sources"]["kaputt"] = {
        "type": "catalog",
        "path": str(tmp_path / "gibt-es-nicht.csv"),
        "priority": 5,
    }
    prepared.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    settings = load_settings(prepared.config_file)

    tender_id = await _with_items(settings)
    result = await research_prices(settings, tender_id)

    assert result.sources_failed and result.sources_failed[0]["source"] == "kaputt"
    assert result.items[0].best_match is not None  # die gute Quelle liefert weiter
    assert any("gestoert" in warning for warning in result.warnings)


async def test_batch_skips_unchanged_tenders(prepared: Settings):
    await _with_items(prepared)

    first = await research_open_tenders(prepared, limit=10)
    assert first.count == 1

    second = await research_open_tenders(prepared, limit=10)
    assert second.count == 0

    third = await research_open_tenders(prepared, limit=10, skip_researched=False)
    assert third.count == 1


async def test_batch_ignores_tenders_without_items(prepared: Settings):
    await run_search(prepared, SearchQuery(max_results=5), only_sources=["fixture"])
    report = await research_open_tenders(prepared, limit=10)
    assert report.count == 0


def test_cli_prices_reports_the_price_picture(prepared: Settings):
    import asyncio

    asyncio.run(_with_items(prepared))

    result = runner.invoke(
        app, ["prices", "fixture:pr-1", "--config", str(prepared.config_file), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["item_count"] == 3
    assert payload["usable_count"] == 1
    assert payload["coverage_percent"] == 33
    monitor = payload["items"][0]
    assert monitor["best_match"]["quote"]["supplier"] == "Muster Distribution"
    assert monitor["best_match"]["quote"]["basis"] == "NET"
    assert monitor["statistics"]["currency"] == "EUR"


def test_cli_prices_requires_a_tender(settings: Settings):
    result = runner.invoke(app, ["prices", "--config", str(settings.config_file)])
    assert result.exit_code == 1
    assert "Tender-ID" in result.output
