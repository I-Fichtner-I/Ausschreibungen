"""Stufe 2B: Analyse-Service und CLI-Befehl."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fpdf import FPDF
from typer.testing import CliRunner

from tender_ai.cli import app
from tender_ai.config import Settings
from tender_ai.core.errors import ConfigError
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.services import analyze_tender, run_search
from tender_ai.sources.base import SearchQuery

runner = CliRunner()
PDF_URL = "https://files.test.invalid/lv.pdf"

DOCUMENT_LINES = (
    "Vergabeunterlagen - Lieferung von 2.000 Monitoren",
    "Die Geraete muessen der DIN EN ISO 9241-307 entsprechen.",
    "Ein Zertifikat nach ISO 9001 ist mit dem Angebot vorzulegen.",
    "Gefordert wird das Umweltzeichen Blauer Engel.",
    "Fabrikat: Muster GmbH Typ MX-27. Nachbauprodukte sind ausgeschlossen.",
    "Zahlungsziel betraegt 60 Tage nach Rechnungseingang.",
    "Bei Lieferverzug wird eine Vertragsstrafe faellig.",
    "Eine Vertragserfuellungsbuergschaft ist zu stellen.",
    "Zuschlagskriterien: Preis 70 Prozent, Qualitaet 30 Prozent.",
)


@pytest.fixture
def pdf_bytes(tmp_path: Path) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=11)
    for line in DOCUMENT_LINES:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "quelle.pdf"
    pdf.output(str(path))
    return path.read_bytes()


@pytest.fixture
def prepared(settings: Settings, sample_tender_file: Path) -> Settings:
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "an-1",
                        "title": "Lieferung von 2.000 Monitoren",
                        "contracting_authority": "Musterstadt",
                        "country": "DEU",
                        "status": "OPEN",
                        "submission_deadline": "2036-10-15T12:00:00+02:00",
                        "estimated_value": 420000.0,
                        "currency": "EUR",
                        "documents": [
                            {
                                "name": "Leistungsverzeichnis",
                                "url": PDF_URL,
                                "media_type": "application/pdf",
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


@respx.mock
async def test_analysis_finds_requirements_and_scores_risk(prepared: Settings, pdf_bytes):
    settings = prepared
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"Content-Type": "application/pdf"}
        )
    )

    result = await analyze_tender(settings, "fixture:an-1")

    kinds = {str(finding.kind) for finding in result.findings}
    assert {"CERTIFICATION", "BRAND_LOCK", "PENALTY", "SECURITY", "AWARD_CRITERIA"} <= kinds
    assert result.risk.score > 40
    codes = {factor.code for factor in result.risk.factors}
    assert "brand_lock_strict" in codes  # keine Gleichwertigkeitsklausel im Dokument
    assert "contract_penalty" in codes
    assert "award_criteria_unclear" not in codes  # Kriterien stehen im Dokument
    # Jeder Fund bleibt auf Dokument und Seite rueckfuehrbar
    for finding in result.findings:
        assert finding.provenance and finding.provenance.document
        assert finding.provenance.page


@respx.mock
async def test_analysis_is_persisted_with_requirements(prepared: Settings, pdf_bytes):
    settings = prepared
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))

    result = await analyze_tender(settings, "fixture:an-1")

    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        risk = repository.risk_for("fixture:an-1")
        assert risk is not None
        assert risk.score == result.risk.score
        assert risk.level == str(result.risk.level)
        assert risk.factors and all("explanation" in factor for factor in risk.factors)

        record = repository.get("fixture:an-1")
        tender = TenderRepository.to_tender(record)
        assert "ISO 9001" in tender.requirements.certifications
        assert tender.requirements.award_criteria


@respx.mock
async def test_analysis_fetches_missing_documents(prepared: Settings, pdf_bytes):
    """Ein Befehl genuegt vom Suchtreffer bis zur Bewertung."""
    settings = prepared
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    route = respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))

    await analyze_tender(settings, "fixture:an-1")
    assert route.call_count == 1

    # Zweiter Lauf nutzt die gespeicherten Extrakte
    await analyze_tender(settings, "fixture:an-1")
    assert route.call_count == 1


async def test_analysis_without_documents_reports_uncertainty(
    settings: Settings, sample_tender_file: Path
):
    """Ohne Unterlagen ist das Risiko hoch, nicht niedrig."""
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    result = await analyze_tender(settings, "fixture:t-1", fetch_missing=False)
    codes = {factor.code for factor in result.risk.factors}
    assert "documents_missing" in codes
    assert result.risk.score > 0
    assert result.findings == []


async def test_unknown_tender_raises(settings: Settings):
    with pytest.raises(ConfigError, match="nicht gefunden"):
        await analyze_tender(settings, "gibtsnicht")


@respx.mock
def test_analyze_cli(prepared: Settings, pdf_bytes):
    settings = prepared
    args = ["--config", str(settings.config_file)]
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"Content-Type": "application/pdf"}
        )
    )
    assert runner.invoke(app, ["search", "--source", "fixture", *args]).exit_code == 0

    result = runner.invoke(app, ["analyze", "fixture:an-1", *args])
    assert result.exit_code == 0, result.output
    assert "Risiko:" in result.output
    assert "Risikofaktoren" in result.output
    # Der Hinweis auf die Grenzen der Auswertung muss sichtbar bleiben; in der
    # schmalen Testkonsole bricht der Satz um, deshalb nur ein Teilstueck.
    assert "Rechtsauskunft" in result.output

    result = runner.invoke(app, ["analyze", "fixture:an-1", "--findings", *args])
    assert result.exit_code == 0
    assert "Fundstellen" in result.output

    result = runner.invoke(app, ["analyze", "fixture:an-1", "--json", *args])
    payload = json.loads(result.stdout)
    assert payload["risk"]["score"] > 0
    assert payload["findings"][0]["document"] and payload["findings"][0]["page"]

    result = runner.invoke(app, ["analyze", "gibtsnicht", *args])
    assert result.exit_code == 1


# --- Stapelanalyse und Sichtbarkeit in list/show --------------------------
@respx.mock
async def test_batch_analysis_skips_already_analysed(prepared: Settings, pdf_bytes):
    from tender_ai.services import analyze_open_tenders

    settings = prepared
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))

    first = await analyze_open_tenders(settings)
    assert first.count == 1
    assert first.failed == []

    # Ohne Aenderung wird nicht erneut ausgewertet
    second = await analyze_open_tenders(settings)
    assert second.count == 0

    third = await analyze_open_tenders(settings, skip_analyzed=False)
    assert third.count == 1
    assert third.as_dict()["results"][0]["score"] == first.analyzed[0].risk.score


async def test_batch_analysis_survives_a_broken_tender(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    """Eine fehlerhafte Ausschreibung darf den Stapel nicht stoppen."""
    from tender_ai.services import analysis as analysis_module

    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])

    original = analysis_module.analyze_tender
    calls: list[str] = []

    async def flaky(settings_arg, tender_id, **kwargs):
        calls.append(tender_id)
        if len(calls) == 1:
            raise RuntimeError("simulierter Analysefehler")
        return await original(settings_arg, tender_id, **kwargs)

    monkeypatch.setattr(analysis_module, "analyze_tender", flaky)
    report = await analysis_module.analyze_open_tenders(settings, fetch_missing=False)

    assert len(calls) == 2
    assert report.count == 1
    assert report.failed and "simulierter" in report.failed[0]["error"]


@respx.mock
def test_risk_is_visible_in_list_and_show(prepared: Settings, pdf_bytes):
    settings = prepared
    args = ["--config", str(settings.config_file)]
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))
    runner.invoke(app, ["search", "--source", "fixture", *args])

    # Vor der Analyse: kein Risiko, aber ein Hinweis wie man es bekommt
    result = runner.invoke(app, ["show", "fixture:an-1", *args])
    assert "Noch nicht analysiert" in result.output

    runner.invoke(app, ["analyze", "fixture:an-1", *args])

    result = runner.invoke(app, ["list", "--json", *args])
    payload = json.loads(result.stdout)
    entry = next(t for t in payload["tenders"] if t["id"] == "fixture:an-1")
    assert entry["risk_score"] > 0
    assert entry["risk_level"] in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")

    result = runner.invoke(app, ["show", "fixture:an-1", *args])
    assert "Risiko:" in result.output
    assert "Noch nicht analysiert" not in result.output


@respx.mock
def test_analyze_all_cli(prepared: Settings, pdf_bytes):
    settings = prepared
    args = ["--config", str(settings.config_file)]
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))
    runner.invoke(app, ["search", "--source", "fixture", *args])

    result = runner.invoke(app, ["analyze", "--all", *args])
    assert result.exit_code == 0, result.output
    assert "Analysierte Ausschreibungen" in result.output

    result = runner.invoke(app, ["analyze", *args])
    assert result.exit_code == 1
    assert "--all" in result.output
