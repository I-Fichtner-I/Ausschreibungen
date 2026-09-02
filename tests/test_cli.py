from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from tender_ai.cli import app
from tender_ai.config import Settings

runner = CliRunner()

TED_URL = "https://api.test.invalid/v3/notices/search"
FEED_URL = "https://feed.test.invalid/rss.xml"


def _args(settings: Settings, *extra: str) -> list[str]:
    return [*extra, "--config", str(settings.config_file)]


def test_init_and_sources(settings: Settings):
    result = runner.invoke(app, _args(settings, "init"))
    assert result.exit_code == 0, result.output
    assert "tender-ai bereit" in result.output

    result = runner.invoke(app, _args(settings, "sources"))
    assert result.exit_code == 0
    for name in ("ted", "feed", "fixture"):
        assert name in result.output


def test_search_fixture_source_and_list(settings: Settings):
    result = runner.invoke(app, _args(settings, "search", "--source", "fixture", "--limit", "10"))
    assert result.exit_code == 0, result.output
    assert "2 neu" in result.output

    result = runner.invoke(app, _args(settings, "list", "--json"))
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["stats"]["tenders_primary"] == 2
    assert {t["source"] for t in payload["tenders"]} == {"fixture"}


def test_search_no_store_leaves_db_empty(settings: Settings):
    result = runner.invoke(
        app, _args(settings, "search", "--source", "fixture", "--no-store", "--json")
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["stored"] is False
    assert len(payload["tenders"]) == 2

    result = runner.invoke(app, _args(settings, "list", "--json"))
    assert json.loads(result.stdout)["stats"]["tenders_primary"] == 0


@respx.mock
def test_doctor_reports_failing_source(settings: Settings):
    respx.post(TED_URL).mock(return_value=httpx.Response(500))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text="<rss></rss>"))
    result = runner.invoke(app, _args(settings, "doctor", "--json"))
    statuses = {entry["name"]: entry for entry in json.loads(result.stdout)}
    assert statuses["fixture"]["ok"] is True
    assert statuses["ted"]["ok"] is False
    assert result.exit_code == 1  # mindestens eine Quelle defekt


def test_show_and_export(settings: Settings, tmp_path: Path):
    runner.invoke(app, _args(settings, "search", "--source", "fixture"))

    result = runner.invoke(app, _args(settings, "show", "fixture:t-1"))
    assert result.exit_code == 0
    assert "Musterstadt" in result.output

    result = runner.invoke(app, _args(settings, "show", "gibt-es-nicht"))
    assert result.exit_code == 1

    target = tmp_path / "export.csv"
    result = runner.invoke(app, _args(settings, "export", str(target)))
    assert result.exit_code == 0
    assert target.is_file()
    assert "Lieferung von 500 Monitoren" in target.read_text(encoding="utf-8-sig")


def test_runs_shows_history(settings: Settings):
    runner.invoke(app, _args(settings, "search", "--source", "fixture"))
    result = runner.invoke(app, _args(settings, "runs"))
    assert result.exit_code == 0
    assert "Rechercherlaeufe" in result.output
    assert "Quellenstatus" in result.output


def test_search_with_unknown_source_fails_clearly(settings: Settings):
    result = runner.invoke(app, _args(settings, "search", "--source", "gibtsnicht"))
    assert result.exit_code == 1
    assert "Keine aktive Quelle" in result.output


def test_markup_in_source_data_is_escaped(sample_tender_file: Path, settings: Settings):
    """T-06: Rich-Markup aus Quelldaten wird literal ausgegeben, nicht interpretiert."""
    import io

    from rich.console import Console

    from tender_ai.cli import _safe, _tender_table
    from tender_ai.models.tender import Tender

    evil = "[bold red]MANIPULIERT[/] [link=https://evil.invalid]klick[/link]"
    assert _safe(evil) == evil.replace("[", "\\[")

    tender = Tender(
        id="x:1", source="x", source_id="1", title=evil, contracting_authority="[unclosed"
    )
    buffer = io.StringIO()
    Console(file=buffer, width=200, force_terminal=False).print(_tender_table([tender]))
    output = buffer.getvalue()
    assert "[bold red]MANIPULIERT" in output
    assert "[unclosed" in output


def test_show_renders_markup_titles_without_error(sample_tender_file: Path, settings: Settings):
    """Der komplette CLI-Pfad darf an Markup in Quelldaten nicht scheitern."""
    payload = json.loads(sample_tender_file.read_text(encoding="utf-8"))
    payload["tenders"][0]["title"] = "[bold red]MANIPULIERT[/] [unclosed"
    sample_tender_file.write_text(json.dumps(payload), encoding="utf-8")

    assert runner.invoke(app, _args(settings, "search", "--source", "fixture")).exit_code == 0
    result = runner.invoke(app, _args(settings, "show", "fixture:t-1"))
    assert result.exit_code == 0, result.output
    assert "MANIPULIERT" in result.output


def test_db_upgrade_command(settings: Settings):
    """T-11: Schema wird ueber Migrationen angelegt und gemeldet."""
    result = runner.invoke(app, _args(settings, "init"))
    assert result.exit_code == 0, result.output
    assert "Schema-Revision" in result.output

    result = runner.invoke(app, _args(settings, "db-upgrade"))
    assert result.exit_code == 0, result.output
    assert "bereits aktuell" in result.output
