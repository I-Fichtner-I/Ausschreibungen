"""Stufe 2: Unterlagen beschaffen und auslesen (Service und CLI)."""

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
from tender_ai.services import fetch_documents, run_search
from tender_ai.sources.base import SearchQuery

runner = CliRunner()

PDF_URL = "https://files.test.invalid/lv.pdf"
LOGIN_URL = "https://portal.test.invalid/geschuetzt"


@pytest.fixture
def pdf_bytes(tmp_path: Path) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 10, "Leistungsverzeichnis Monitore", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 10, "Position 1: 500 Stueck", new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "quelle.pdf"
    pdf.output(str(path))
    return path.read_bytes()


@pytest.fixture
def tender_with_documents(settings: Settings, sample_tender_file: Path) -> Settings:
    """Fixture-Quelle mit einem oeffentlichen und einem geschuetzten Dokument."""
    sample_tender_file.write_text(
        json.dumps(
            {
                "tenders": [
                    {
                        "source_id": "doc-1",
                        "title": "Lieferung von Monitoren",
                        "contracting_authority": "Musterstadt",
                        "country": "DEU",
                        "status": "OPEN",
                        "documents": [
                            {
                                "name": "Leistungsverzeichnis",
                                "url": PDF_URL,
                                "media_type": "application/pdf",
                                "access": "PUBLIC",
                            },
                            {
                                "name": "Anlage im Vergabeportal",
                                "url": LOGIN_URL,
                                "access": "REGISTRATION",
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return settings


async def _ingest(settings: Settings) -> None:
    await run_search(settings, SearchQuery(max_results=5), only_sources=["fixture"])


@respx.mock
async def test_fetch_downloads_extracts_and_stores(tender_with_documents: Settings, pdf_bytes):
    settings = tender_with_documents
    await _ingest(settings)
    route = respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"Content-Type": "application/pdf"}
        )
    )

    report = await fetch_documents(settings, "fixture:doc-1")

    assert report.downloaded == 1
    assert report.extracted == 1
    assert report.skipped_restricted == 1  # geschuetzte Anlage wird nicht abgerufen
    assert route.call_count == 1
    document = report.documents[0]
    assert document.extractor == "pdf" and document.status == "OK"
    assert Path(document.local_path or "").is_file()

    with session_scope(settings.database_url) as session:
        extracts = TenderRepository(session, settings.dedup).extracts_for("fixture:doc-1")
        assert len(extracts) == 1
        assert "Leistungsverzeichnis Monitore" in (extracts[0].text or "")
        assert extracts[0].status == "OK"
        assert extracts[0].checksum_sha256


@respx.mock
async def test_protected_document_is_never_requested(tender_with_documents: Settings, pdf_bytes):
    settings = tender_with_documents
    await _ingest(settings)
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))
    login_route = respx.get(LOGIN_URL)

    await fetch_documents(settings, "fixture:doc-1")
    assert login_route.call_count == 0


@respx.mock
async def test_second_run_reuses_local_file(tender_with_documents: Settings, pdf_bytes):
    settings = tender_with_documents
    await _ingest(settings)
    route = respx.get(PDF_URL).mock(return_value=httpx.Response(200, content=pdf_bytes))

    await fetch_documents(settings, "fixture:doc-1")
    await fetch_documents(settings, "fixture:doc-1")
    assert route.call_count == 1, "vorhandene Datei darf nicht erneut geladen werden"

    await fetch_documents(settings, "fixture:doc-1", force=True)
    assert route.call_count == 2


@respx.mock
async def test_failed_download_is_noted_not_raised(tender_with_documents: Settings):
    settings = tender_with_documents
    await _ingest(settings)
    respx.get(PDF_URL).mock(return_value=httpx.Response(404))

    report = await fetch_documents(settings, "fixture:doc-1")
    assert report.downloaded == 0
    assert report.documents == [] or report.documents[0].status != "OK"


async def test_unknown_tender_raises_config_error(settings: Settings):
    with pytest.raises(ConfigError, match="nicht gefunden"):
        await fetch_documents(settings, "gibtsnicht")


@respx.mock
def test_documents_cli(tender_with_documents: Settings, pdf_bytes):
    settings = tender_with_documents
    args = ["--config", str(settings.config_file)]
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"Content-Type": "application/pdf"}
        )
    )
    assert runner.invoke(app, ["search", "--source", "fixture", *args]).exit_code == 0

    result = runner.invoke(app, ["documents", "fixture:doc-1", *args])
    assert result.exit_code == 0, result.output
    # Die Tabelle bricht Namen in der schmalen Testkonsole um - die Zusammen-
    # fassung ist die stabile Zusicherung, den Inhalt prueft die JSON-Ausgabe.
    assert "Geladen: 1" in result.output
    assert "nicht oeffentlich: 1" in result.output
    assert "Lieferung von Monitoren" in result.output

    result = runner.invoke(app, ["documents", "fixture:doc-1", "--json", *args])
    payload = json.loads(result.stdout)
    assert payload["downloaded"] == 1 and payload["skipped_restricted"] == 1
    assert payload["documents"][0]["name"] == "Leistungsverzeichnis"
    assert payload["documents"][0]["status"] == "OK"

    result = runner.invoke(app, ["documents", "gibtsnicht", *args])
    assert result.exit_code == 1
    assert "nicht gefunden" in result.output
