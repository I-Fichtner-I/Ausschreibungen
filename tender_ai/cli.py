"""Kommandozeile von tender-ai (Stufe 1: Recherche).

tender-ai init                 Verzeichnisse und Datenbank anlegen
tender-ai sources              konfigurierte Quellen anzeigen
tender-ai doctor               Quellen auf Erreichbarkeit pruefen
tender-ai search               Ausschreibungen recherchieren (live)
tender-ai list                 gespeicherte Ausschreibungen anzeigen
tender-ai show <id>            Details einer Ausschreibung
tender-ai documents <id>       Vergabeunterlagen laden und auslesen
tender-ai analyze <id>         Anforderungen erkennen und Risiko bewerten
tender-ai analyze --all        alle laufenden Ausschreibungen bewerten
tender-ai export               Ergebnisse als JSON/CSV/XLSX ausgeben
tender-ai runs                 letzte Rechercherlaeufe
tender-ai cache-clear          HTTP-Cache leeren
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .config import Settings, load_settings
from .core.errors import ConfigError
from .core.logging import configure_logging, get_logger
from .database.migrations import current_revision, ensure_current_schema, head_revision
from .database.repository import TenderRepository
from .database.session import session_scope
from .export.exporters import EXPORT_FORMATS, export_tenders
from .models.common import display as _display
from .models.tender import Tender
from .services import (
    analyze_open_tenders,
    analyze_tender,
    check_sources,
    extract_items_for_open_tenders,
    extract_tender_items,
    fetch_documents,
    research_and_store,
    research_open_tenders,
    run_search,
)
from .sources.base import SearchQuery
from .sources.registry import available_types

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Procurement Intelligence Agent - Stufe 1: automatisierte Ausschreibungsrecherche.",
)
console = Console()
log = get_logger(__name__)


# --- Hilfsfunktionen ---------------------------------------------------------
def _settings(config: Path | None = None) -> Settings:
    settings = load_settings(config) if config else load_settings()
    configure_logging(settings.logging.level, settings.logging.format)
    settings.ensure_directories()
    return settings


def _safe(value: Any, *, missing: str = "UNKNOWN") -> str:
    """Fremddaten fuer die Terminalausgabe rendern.

    ``rich`` interpretiert eckige Klammern als Markup - ein Ausschreibungstitel
    wie ``[bold red]...[/]`` wuerde sonst die Ausgabe umfaerben oder Links
    einschleusen. Deshalb wird jeder Wert aus einer Quelle escaped.
    """
    return escape(_display(value, missing=missing))


def _query_from_options(
    settings: Settings,
    keywords: list[str] | None,
    cpv: list[str] | None,
    countries: list[str] | None,
    days: int | None,
    min_deadline_days: int | None,
    limit: int | None,
) -> SearchQuery:
    query = SearchQuery.from_config(settings.search)
    if keywords:
        query.keywords = list(keywords)
    if cpv:
        query.cpv_codes = list(cpv)
    if countries:
        query.countries = list(countries)
    if days is not None:
        query.published_after = datetime.now(UTC).date() - timedelta(days=days)
    if min_deadline_days is not None:
        query.deadline_after = (
            datetime.now(UTC) + timedelta(days=min_deadline_days) if min_deadline_days > 0 else None
        )
    if limit is not None:
        query.max_results = limit
    return query


RISK_COLOURS = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "VERY_HIGH": "bold red"}


def _risk_cell(score: int | None, level: str | None) -> str:
    """Risiko kompakt und farbig; ohne Analyse bleibt es leer statt 0."""
    if score is None or level is None:
        return "[dim]-[/dim]"
    colour = RISK_COLOURS.get(level, "white")
    return f"[{colour}]{score}[/{colour}]"


def _tender_table(
    tenders: list[Tender],
    title: str = "Ausschreibungen",
    risks: dict[str, tuple[int, str]] | None = None,
) -> Table:
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("Quelle", style="cyan", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True, max_width=22)
    table.add_column("Titel", overflow="fold", max_width=60)
    table.add_column("Vergabestelle", overflow="fold", max_width=30)
    table.add_column("Frist", no_wrap=True)
    table.add_column("Tage", justify="right", no_wrap=True)
    table.add_column("Risiko", justify="right", no_wrap=True)
    table.add_column("Volumen", justify="right", no_wrap=True)
    risks = risks or {}
    for tender in tenders:
        days_left = tender.days_until_deadline
        days_text = _safe(days_left)
        style = ""
        if isinstance(days_left, int):
            style = "red" if days_left < 3 else ("yellow" if days_left < 10 else "green")
        value = (
            f"{tender.estimated_value:,.0f} {tender.currency or ''}".strip()
            if tender.estimated_value is not None
            else "UNKNOWN"
        )
        table.add_row(
            escape(tender.source),
            escape(tender.source_id[:22]),
            _safe(tender.title),
            _safe(tender.contracting_authority),
            _safe(tender.submission_deadline),
            f"[{style}]{days_text}[/{style}]" if style else days_text,
            _risk_cell(*risks[tender.id]) if tender.id in risks else "[dim]-[/dim]",
            value,
        )
    return table


def _print_source_reports(report: Any) -> None:
    table = Table(title="Quellen", header_style="bold")
    table.add_column("Quelle", style="cyan")
    table.add_column("Typ")
    table.add_column("Status")
    table.add_column("Gefunden", justify="right")
    table.add_column("Neu", justify="right")
    table.add_column("Aktualisiert", justify="right")
    table.add_column("Dubletten", justify="right")
    table.add_column("Fehlgeschlagen", justify="right")
    table.add_column("Dauer (s)", justify="right")
    for source_report in report.sources:
        failed = str(source_report.failed)
        table.add_row(
            escape(source_report.name),
            escape(source_report.type),
            "[green]OK[/green]" if source_report.ok else "[red]FEHLER[/red]",
            str(source_report.found),
            str(source_report.new),
            str(source_report.updated),
            str(source_report.duplicates),
            f"[red]{failed}[/red]" if source_report.failed else failed,
            f"{source_report.duration_seconds:.2f}",
        )
    console.print(table)
    for error in report.errors:
        where = escape(str(error["source"]))
        if error.get("tender_id"):
            where = f"{where}, Datensatz {escape(str(error['tender_id']))}"
        console.print(f"[red]Fehler in Quelle '{where}':[/red] {escape(str(error['error']))}")


# --- Kommandos ---------------------------------------------------------------
@app.command()
def init(config: Path | None = typer.Option(None, "--config", help="Pfad zu config.yaml")) -> None:
    """Verzeichnisse anlegen, Datenbankschema erzeugen, Konfiguration pruefen."""
    settings = _settings(config)
    revision = ensure_current_schema(settings.database_url)
    console.print(
        Panel.fit(
            f"Datenverzeichnis: {settings.data_dir}\n"
            f"Datenbank:        {settings.database_url}\n"
            f"Schema-Revision:  {revision}\n"
            f"Quellen aktiv:    {', '.join(settings.enabled_sources()) or 'keine'}\n"
            f"Quelltypen:       {', '.join(available_types())}",
            title="tender-ai bereit",
        )
    )
    if not Path(".env").is_file():
        console.print(
            "[yellow]Hinweis:[/yellow] keine .env gefunden - fuer API-Schluessel "
            ".env.example kopieren: cp .env.example .env"
        )


@app.command()
def sources(config: Path | None = typer.Option(None, "--config")) -> None:
    """Konfigurierte Quellen anzeigen."""
    settings = _settings(config)
    table = Table(title="Konfigurierte Quellen", header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Typ")
    table.add_column("Aktiv")
    table.add_column("Prioritaet", justify="right")
    table.add_column("Rate (req/s)", justify="right")
    for name, source_config in sorted(settings.sources.items(), key=lambda item: item[1].priority):
        table.add_row(
            escape(name),
            escape(source_config.type),
            "[green]ja[/green]" if source_config.enabled else "[dim]nein[/dim]",
            str(source_config.priority),
            _safe(source_config.requests_per_second or settings.http.requests_per_second),
        )
    console.print(table)
    console.print(f"Verfuegbare Quelltypen: {', '.join(available_types())}")


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config"),
    source: list[str] | None = typer.Option(None, "--source", "-s", help="nur diese Quelle(n)"),
    json_output: bool = typer.Option(False, "--json", help="Ergebnis als JSON ausgeben"),
) -> None:
    """Erreichbarkeit und Parsing der Quellen pruefen (Probeabruf)."""
    settings = _settings(config)
    results = [status.as_dict() for status in asyncio.run(check_sources(settings, source))]

    if json_output:
        console.print_json(jsonlib.dumps(results, ensure_ascii=False))
        raise typer.Exit(0 if all(r["ok"] for r in results) else 1)

    if not results:
        console.print("[yellow]Keine Quellen konfiguriert oder ausgewaehlt.[/yellow]")
        raise typer.Exit(1)

    table = Table(title="Quellen-Health-Check", header_style="bold")
    table.add_column("Quelle", style="cyan")
    table.add_column("Typ")
    table.add_column("Status")
    table.add_column("Meldung", overflow="fold", max_width=70)
    table.add_column("Dauer (s)", justify="right")
    for result in results:
        table.add_row(
            escape(str(result["name"])),
            escape(str(result["type"])),
            "[green]OK[/green]" if result["ok"] else "[red]FEHLER[/red]",
            escape(str(result["message"])),
            str(result["duration_seconds"]),
        )
    console.print(table)
    raise typer.Exit(0 if all(r["ok"] for r in results) else 1)


@app.command()
def search(
    config: Path | None = typer.Option(None, "--config"),
    keyword: list[str] | None = typer.Option(
        None, "--keyword", "-k", help="Suchbegriff (mehrfach moeglich)"
    ),
    cpv: list[str] | None = typer.Option(None, "--cpv", help="CPV-Code (mehrfach moeglich)"),
    country: list[str] | None = typer.Option(None, "--country", help="Land, ISO-3 (z. B. DEU)"),
    source: list[str] | None = typer.Option(None, "--source", "-s", help="nur diese Quelle(n)"),
    days: int | None = typer.Option(None, "--days", help="Veroeffentlichung der letzten N Tage"),
    min_deadline_days: int | None = typer.Option(
        None, "--min-deadline-days", help="nur Ausschreibungen mit mind. N Tagen Restfrist"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="max. Treffer je Quelle"),
    store: bool = typer.Option(
        True, "--store/--no-store", help="Ergebnisse in der Datenbank speichern"
    ),
    download_docs: bool = typer.Option(
        False, "--download-docs", help="frei zugaengliche Unterlagen herunterladen"
    ),
    export: Path | None = typer.Option(None, "--export", help="Ergebnisse in Datei exportieren"),
    export_format: str = typer.Option(
        "json", "--format", help=f"Exportformat: {', '.join(EXPORT_FORMATS)}"
    ),
    json_output: bool = typer.Option(False, "--json", help="Ergebnis als JSON ausgeben"),
) -> None:
    """Ausschreibungen bei den konfigurierten Quellen recherchieren."""
    settings = _settings(config)
    query = _query_from_options(settings, keyword, cpv, country, days, min_deadline_days, limit)

    try:
        report = asyncio.run(
            run_search(
                settings,
                query,
                only_sources=source,
                store=store,
                download_documents=download_docs,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        payload = report.as_dict()
        payload["tenders"] = [t.model_dump(mode="json") for t in report.tenders]
        console.print_json(jsonlib.dumps(payload, ensure_ascii=False, default=str))
    else:
        _print_source_reports(report)
        if report.tenders:
            console.print(_tender_table(report.tenders, title=f"{len(report.tenders)} Treffer"))
        else:
            console.print("[yellow]Keine Treffer.[/yellow]")
        if store:
            console.print(
                f"Gespeichert: [green]{report.new} neu[/green], "
                f"{report.updated} aktualisiert, {report.duplicates} Dublette(n)."
            )
        else:
            console.print("[dim]--no-store: nichts gespeichert.[/dim]")

    if export is not None:
        path = export_tenders(report.tenders, export, export_format)
        console.print(f"Export geschrieben: [cyan]{path}[/cyan]")

    if report.errors and not report.tenders:
        raise typer.Exit(1)


@app.command("list")
def list_tenders(
    config: Path | None = typer.Option(None, "--config"),
    limit: int = typer.Option(25, "--limit", "-n"),
    source: list[str] | None = typer.Option(None, "--source", "-s"),
    search_text: str | None = typer.Option(None, "--search", help="Titel/Vergabestelle enthaelt"),
    open_only: bool = typer.Option(False, "--open", help="nur laufende Ausschreibungen"),
    order_by: str = typer.Option("deadline", "--order", help="deadline | published | value | seen"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Gespeicherte Ausschreibungen aus der Datenbank anzeigen."""
    settings = _settings(config)
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        records = repository.list_tenders(
            limit=limit,
            sources=source,
            search=search_text,
            open_only=open_only,
            order_by=order_by,
        )
        tenders = [TenderRepository.to_tender(record) for record in records]
        risks = {
            record.id: (record.risk_analysis.score, record.risk_analysis.level)
            for record in records
            if record.risk_analysis is not None
        }
        stats = repository.stats()

    if json_output:
        console.print_json(
            jsonlib.dumps(
                {
                    "stats": stats,
                    "tenders": [
                        {
                            **tender.model_dump(mode="json"),
                            "risk_score": risks.get(tender.id, (None, None))[0],
                            "risk_level": risks.get(tender.id, (None, None))[1],
                        }
                        for tender in tenders
                    ],
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return

    console.print(
        _tender_table(tenders, title=f"Gespeicherte Ausschreibungen ({len(tenders)})", risks=risks)
    )
    console.print(
        f"Gesamt: {stats['tenders_primary']} (inkl. Dubletten: {stats['tenders_total']}), "
        f"laufend: {stats['tenders_open']}"
    )


@app.command()
def show(
    tender_id: str = typer.Argument(..., help="Tender-ID (z. B. ted:00123456-2026) oder Quell-ID"),
    config: Path | None = typer.Option(None, "--config"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Details einer gespeicherten Ausschreibung anzeigen."""
    settings = _settings(config)
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(tender_id)
        if record is None:
            console.print(f"[red]Nicht gefunden:[/red] {tender_id}")
            raise typer.Exit(1)
        tender = TenderRepository.to_tender(record)
        aliases = repository.aliases_for(record.id)
        changes = repository.changes_for(record.id, limit=50)
        risk_record = repository.risk_for(record.id)
        risk_summary = (
            (risk_record.score, risk_record.level, list(risk_record.factors or []))
            if risk_record
            else None
        )
        item_extraction = repository.item_extraction_for(record.id)
        item_summary = (
            (
                item_extraction.item_count,
                item_extraction.priceable_count,
                item_extraction.average_confidence,
            )
            if item_extraction
            else None
        )

    if json_output:
        console.print_json(
            jsonlib.dumps(tender.model_dump(mode="json"), ensure_ascii=False, default=str)
        )
        return

    body = "\n".join(
        [
            f"[bold]{_safe(tender.title)}[/bold]",
            "",
            f"Vergabestelle:  {_safe(tender.contracting_authority)}",
            f"Quelle:         {tender.source} ({_safe(tender.source_url)})",
            f"Amtliche Nr.:   {_safe(tender.national_id)}",
            f"Land/Region:    {_safe(tender.country)} / {_safe(tender.region)}",
            f"CPV:            {_safe(tender.cpv_codes)}",
            f"Veroeffentlicht:{_safe(tender.publication_date)}",
            (
                f"Angebotsfrist:  {_safe(tender.submission_deadline)} "
                f"(in {_safe(tender.days_until_deadline)} Tagen)"
            ),
            f"Bindefrist:     {_safe(tender.binding_period_end)}",
            f"Lieferfrist:    {_safe(tender.delivery_deadline)}",
            (
                f"Volumen:        {_safe(tender.estimated_value)} "
                f"{_safe(tender.currency, missing='')}"
            ),
            f"Verfahren:      {_safe(tender.procedure_type)}",
            f"Status:         {tender.status}",
            "",
            f"{_safe(tender.description)}",
        ]
    )
    console.print(Panel(body, title=escape(tender.id), expand=True))

    if tender.lots:
        table = Table(title="Lose", header_style="bold")
        table.add_column("Los")
        table.add_column("Titel", overflow="fold")
        table.add_column("Volumen", justify="right")
        for lot in tender.lots:
            table.add_row(_safe(lot.lot_id), _safe(lot.title), _safe(lot.estimated_value))
        console.print(table)

    if tender.documents:
        table = Table(title="Dokumente", header_style="bold")
        table.add_column("Name", overflow="fold")
        table.add_column("Zugriff")
        table.add_column("URL", overflow="fold")
        table.add_column("Lokal", overflow="fold")
        for document in tender.documents:
            table.add_row(
                _safe(document.name),
                str(document.access),
                _safe(document.url),
                _safe(document.local_path),
            )
        console.print(table)

    if aliases:
        table = Table(title="Weitere Fundstellen (Dubletten)", header_style="bold")
        table.add_column("Quelle")
        table.add_column("Quell-ID")
        table.add_column("Grund")
        table.add_column("Konfidenz", justify="right")
        for alias in aliases:
            table.add_row(
                alias.source,
                alias.source_id,
                _safe(alias.match_reason),
                _safe(alias.match_confidence),
            )
        console.print(table)

    if changes:
        table = Table(title="Aenderungen", header_style="bold")
        table.add_column("Zeitpunkt")
        table.add_column("Feld")
        table.add_column("Alt", overflow="fold")
        table.add_column("Neu", overflow="fold")
        for change in changes[:20]:
            table.add_row(
                _safe(change.detected_at),
                escape(change.field),
                _safe(change.old_value),
                _safe(change.new_value),
            )
        console.print(table)

    if risk_summary is not None:
        score, level, factors = risk_summary
        colour = RISK_COLOURS.get(level, "white")
        risk_table = Table(
            title=f"Risiko: {score}/100 ({level})", header_style="bold", title_style=colour
        )
        risk_table.add_column("Punkte", justify="right")
        risk_table.add_column("Faktor")
        risk_table.add_column("Begruendung", overflow="fold")
        for factor in factors[:8]:
            risk_table.add_row(
                str(factor.get("points", "")),
                _safe(factor.get("label")),
                _safe(factor.get("explanation")),
            )
        console.print(risk_table)
    else:
        console.print(
            "[dim]Noch nicht analysiert - 'tender-ai analyze " + escape(tender.id) + "'[/dim]"
        )

    if item_summary is not None:
        count, priceable, confidence = item_summary
        console.print(
            f"Positionen: [bold]{count}[/bold], davon kalkulierbar: {priceable}, "
            f"mittlere Konfidenz: {_confidence_cell(confidence)}"
        )
    else:
        console.print(
            "[dim]Positionen noch nicht erkannt - 'tender-ai items " + escape(tender.id) + "'[/dim]"
        )

    if tender.notes:
        console.print(
            Panel("\n".join(f"- {escape(note)}" for note in tender.notes), title="Hinweise")
        )


@app.command()
def export(
    output: Path = typer.Argument(..., help="Zieldatei"),
    config: Path | None = typer.Option(None, "--config"),
    export_format: str | None = typer.Option(
        None, "--format", help=f"{', '.join(EXPORT_FORMATS)} (Default: aus Dateiendung)"
    ),
    limit: int = typer.Option(1000, "--limit", "-n"),
    source: list[str] | None = typer.Option(None, "--source", "-s"),
    open_only: bool = typer.Option(False, "--open"),
) -> None:
    """Gespeicherte Ausschreibungen exportieren (JSON, CSV, XLSX)."""
    settings = _settings(config)
    fmt = (export_format or output.suffix.lstrip(".") or "json").lower()
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        records = repository.list_tenders(limit=limit, sources=source, open_only=open_only)
        tenders = [TenderRepository.to_tender(record) for record in records]
    path = export_tenders(tenders, output, fmt)
    console.print(
        f"[green]{len(tenders)}[/green] Ausschreibungen exportiert nach [cyan]{path}[/cyan]"
    )


@app.command()
def runs(
    config: Path | None = typer.Option(None, "--config"),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Letzte Rechercherlaeufe und Quellenstatus anzeigen."""
    settings = _settings(config)
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        run_records = repository.last_runs(limit)
        states = repository.source_states()

    table = Table(title="Rechercherlaeufe", header_style="bold")
    table.add_column("Start")
    table.add_column("Quellen")
    table.add_column("Gefunden", justify="right")
    table.add_column("Neu", justify="right")
    table.add_column("Aktualisiert", justify="right")
    table.add_column("Dubletten", justify="right")
    table.add_column("Fehler", justify="right")
    for run in run_records:
        table.add_row(
            _safe(run.started_at),
            ", ".join(run.sources or []),
            str(run.found),
            str(run.new),
            str(run.updated),
            str(run.duplicates),
            str(len(run.errors or [])),
        )
    console.print(table)

    if states:
        state_table = Table(title="Quellenstatus", header_style="bold")
        state_table.add_column("Quelle", style="cyan")
        state_table.add_column("Letzter Lauf")
        state_table.add_column("Letzter Erfolg")
        state_table.add_column("Treffer", justify="right")
        state_table.add_column("Fehler in Folge", justify="right")
        state_table.add_column("Letzter Fehler", overflow="fold", max_width=50)
        for state in states:
            state_table.add_row(
                escape(state.name),
                _safe(state.last_run_at),
                _safe(state.last_success_at),
                str(state.last_result_count),
                str(state.consecutive_failures),
                _safe(state.last_error, missing="-"),
            )
        console.print(state_table)


@app.command()
def documents(
    tender_id: str = typer.Argument(..., help="Tender-ID (z. B. ted:00123456-2026) oder Quell-ID"),
    config: Path | None = typer.Option(None, "--config"),
    extract: bool = typer.Option(
        True, "--extract/--no-extract", help="Text und Tabellen aus den Unterlagen auslesen"
    ),
    force: bool = typer.Option(
        False, "--force", help="bereits vorhandene Dateien erneut herunterladen"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Vergabeunterlagen einer Ausschreibung laden und auslesen (Stufe 2)."""
    settings = _settings(config)
    try:
        report = asyncio.run(fetch_documents(settings, tender_id, extract=extract, force=force))
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        console.print_json(jsonlib.dumps(report.as_dict(), ensure_ascii=False, default=str))
        return

    console.print(
        Panel.fit(
            f"[bold]{_safe(report.title)}[/bold]\n{escape(report.tender_id)}",
            title="Vergabeunterlagen",
        )
    )
    if not report.documents:
        console.print("[yellow]Keine abrufbaren Unterlagen gefunden.[/yellow]")
    else:
        table = Table(header_style="bold")
        table.add_column("Dokument", overflow="fold", max_width=40)
        table.add_column("Zugriff")
        table.add_column("Extraktor")
        table.add_column("Status")
        table.add_column("Seiten", justify="right")
        table.add_column("Tabellen", justify="right")
        table.add_column("Zeichen", justify="right")
        for document in report.documents:
            status = document.status or "-"
            style = (
                "green"
                if status == "OK"
                else ("yellow" if status in ("PARTIAL", "EMPTY") else "red")
            )
            table.add_row(
                _safe(document.name or document.local_path),
                _safe(document.access),
                _safe(document.extractor),
                f"[{style}]{escape(status)}[/{style}]",
                str(document.page_count),
                str(document.table_count),
                f"{document.character_count:,}".replace(",", "."),
            )
        console.print(table)

    console.print(
        f"Geladen: [green]{report.downloaded}[/green], ausgelesen: {report.extracted}, "
        f"fehlgeschlagen: {report.failed}, nicht oeffentlich: {report.skipped_restricted}"
    )
    for document in report.documents:
        if document.note:
            console.print(
                f"[yellow]Hinweis[/yellow] {_safe(document.name)}: {_safe(document.note)}"
            )


@app.command()
def analyze(
    tender_id: str | None = typer.Argument(
        None, help="Tender-ID oder Quell-ID; entfaellt bei --all"
    ),
    config: Path | None = typer.Option(None, "--config"),
    fetch: bool = typer.Option(
        True, "--fetch/--no-fetch", help="fehlende Unterlagen vorher nachladen"
    ),
    show_findings: bool = typer.Option(
        False, "--findings", help="alle erkannten Hinweise mit Fundstelle auflisten"
    ),
    analyze_all: bool = typer.Option(
        False, "--all", help="alle laufenden Ausschreibungen analysieren"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="max. Ausschreibungen bei --all"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Unterlagen auswerten: Anforderungen erkennen und Risiko bewerten (Stufe 2)."""
    settings = _settings(config)

    if analyze_all:
        report = asyncio.run(analyze_open_tenders(settings, limit=limit, fetch_missing=fetch))
        if json_output:
            console.print_json(jsonlib.dumps(report.as_dict(), ensure_ascii=False, default=str))
            return
        table = Table(title=f"Analysierte Ausschreibungen ({report.count})", header_style="bold")
        table.add_column("Risiko", justify="right")
        table.add_column("Stufe")
        table.add_column("ID", style="dim", overflow="fold")
        table.add_column("Hinweise", justify="right")
        for result in sorted(report.analyzed, key=lambda item: -item.risk.score):
            level = str(result.risk.level)
            table.add_row(
                _risk_cell(result.risk.score, level),
                escape(level),
                escape(result.tender_id),
                str(len(result.findings)),
            )
        console.print(table)
        for failure in report.failed:
            console.print(
                f"[red]Analyse fehlgeschlagen[/red] {escape(failure['tender_id'])}: "
                f"{escape(failure['error'])}"
            )
        return

    if not tender_id:
        console.print("[red]Bitte eine Tender-ID angeben oder --all verwenden.[/red]")
        raise typer.Exit(1)

    try:
        result = asyncio.run(analyze_tender(settings, tender_id, fetch_missing=fetch))
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        console.print_json(jsonlib.dumps(result.as_dict(), ensure_ascii=False, default=str))
        return

    risk = result.risk
    colour = {
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "red",
        "VERY_HIGH": "bold red",
    }[str(risk.level)]
    console.print(
        Panel.fit(
            f"Risiko: [{colour}]{risk.score}/100 ({escape(str(risk.level))})[/{colour}]\n"
            f"Ausgewertet: {risk.documents_analyzed} Dokument(e), "
            f"{risk.characters_analyzed:,} Zeichen".replace(",", ".")
            + (
                f"\nNicht auswertbar: {risk.documents_unreadable}"
                if risk.documents_unreadable
                else ""
            ),
            title=f"Analyse {escape(result.tender_id)}",
        )
    )

    if risk.factors:
        table = Table(title="Risikofaktoren", header_style="bold")
        table.add_column("Punkte", justify="right")
        table.add_column("Faktor")
        table.add_column("Begruendung", overflow="fold")
        for factor in risk.top_factors:
            table.add_row(str(factor.points), _safe(factor.label), _safe(factor.explanation))
        console.print(table)
    else:
        console.print("[green]Keine Risikofaktoren erkannt.[/green]")

    counts: dict[str, int] = {}
    for finding in result.findings:
        counts[str(finding.kind)] = counts.get(str(finding.kind), 0) + 1
    if counts:
        summary = Table(title="Erkannte Hinweise", header_style="bold")
        summary.add_column("Art")
        summary.add_column("Anzahl", justify="right")
        for kind, count in sorted(counts.items(), key=lambda item: -item[1]):
            summary.add_row(escape(kind), str(count))
        console.print(summary)
    else:
        console.print(
            "[yellow]Keine Anforderungen erkannt[/yellow] - Unterlagen pruefen "
            "(moeglicherweise Scans ohne Texterkennung)."
        )

    if show_findings:
        detail = Table(title="Fundstellen", header_style="bold")
        detail.add_column("Art")
        detail.add_column("Dokument", overflow="fold", max_width=24)
        detail.add_column("Seite", justify="right")
        detail.add_column("Beleg", overflow="fold")
        for finding in result.findings:
            provenance = finding.provenance
            detail.add_row(
                escape(str(finding.kind)),
                _safe(provenance.document if provenance else None),
                _safe(provenance.page if provenance else None),
                _safe(finding.evidence()),
            )
        console.print(detail)

    console.print(
        "[dim]Regelbasierte Auswertung: jeder Hinweis ist ein Fund im Text, "
        "keine Rechtsauskunft. Vor einer Teilnahme im Original pruefen.[/dim]"
    )


@app.command("db-upgrade")
def db_upgrade(config: Path | None = typer.Option(None, "--config")) -> None:
    """Datenbankschema auf den aktuellen Stand bringen (Alembic)."""
    settings = _settings(config)
    before = current_revision(settings.database_url)
    after = ensure_current_schema(settings.database_url)
    head = head_revision(settings.database_url)
    if before == after:
        console.print(f"Schema bereits aktuell (Revision {after}).")
    else:
        console.print(f"Schema migriert: [dim]{before}[/dim] -> [green]{after}[/green]")
    if after != head:  # pragma: no cover - nur bei fehlgeschlagener Migration
        console.print(f"[red]Achtung:[/red] erwartete Revision {head}, erreicht {after}")
        raise typer.Exit(1)


def _confidence_cell(confidence: int) -> str:
    """Konfidenz einfaerben - unter 50 ist die Zeile Handarbeit."""
    colour = "green" if confidence >= 75 else ("yellow" if confidence >= 50 else "red")
    return f"[{colour}]{confidence}[/{colour}]"


def _coverage_cell(percent: int) -> str:
    """Abdeckung einfaerben - unter 50 Prozent traegt keine Kalkulation."""
    colour = "green" if percent >= 80 else ("yellow" if percent >= 50 else "red")
    return f"[{colour}]{percent} %[/{colour}]"


def _quantity_cell(item: Any) -> str:
    """Menge inklusive Schaetz-Kennzeichnung; unbekannt bleibt UNKNOWN."""
    if item.quantity is None:
        return "[dim]UNKNOWN[/dim]"
    number = f"{item.quantity:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    number = number.removesuffix(",00")
    return f"~{number}" if item.quantity_estimated else number


@app.command()
def items(
    tender_id: str | None = typer.Argument(
        None, help="Tender-ID oder Quell-ID; entfaellt bei --all"
    ),
    config: Path | None = typer.Option(None, "--config"),
    fetch: bool = typer.Option(
        True, "--fetch/--no-fetch", help="fehlende Unterlagen vorher nachladen"
    ),
    extract_all: bool = typer.Option(
        False, "--all", help="alle laufenden Ausschreibungen auswerten"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="max. Ausschreibungen bei --all"),
    min_confidence: int = typer.Option(
        0, "--min-confidence", help="nur Positionen ab dieser Erkennungs-Konfidenz"
    ),
    show_evidence: bool = typer.Option(
        False, "--evidence", help="Fundstelle und Originalzeile je Position anzeigen"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Positionen aus den Vergabeunterlagen erkennen (Stufe 3)."""
    settings = _settings(config)

    if extract_all:
        report = asyncio.run(
            extract_items_for_open_tenders(settings, limit=limit, fetch_missing=fetch)
        )
        if json_output:
            console.print_json(jsonlib.dumps(report.as_dict(), ensure_ascii=False, default=str))
            return
        title = f"Artikelerkennung ({report.count} Ausschreibungen, {report.item_count} Positionen)"
        table = Table(title=title, header_style="bold")
        table.add_column("Positionen", justify="right")
        table.add_column("davon kalkulierbar", justify="right")
        table.add_column("Konfidenz", justify="right")
        table.add_column("ID", style="dim", overflow="fold")
        for result in sorted(report.extracted, key=lambda item: -item.item_count):
            table.add_row(
                str(result.item_count),
                str(result.priceable_count),
                _confidence_cell(result.average_confidence),
                escape(result.tender_id),
            )
        console.print(table)
        for failure in report.failed:
            console.print(
                f"[red]Artikelerkennung fehlgeschlagen[/red] {escape(failure['tender_id'])}: "
                f"{escape(failure['error'])}"
            )
        return

    if not tender_id:
        console.print("[red]Bitte eine Tender-ID angeben oder --all verwenden.[/red]")
        raise typer.Exit(1)

    try:
        result = asyncio.run(extract_tender_items(settings, tender_id, fetch_missing=fetch))
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    shown = [item for item in result.items if item.confidence >= min_confidence]
    if json_output:
        payload = result.as_dict()
        payload["items"] = [item.as_dict() for item in shown]
        console.print_json(jsonlib.dumps(payload, ensure_ascii=False, default=str))
        return

    console.print(
        Panel.fit(
            f"Positionen: [bold]{result.item_count}[/bold], "
            f"kalkulierbar: {result.priceable_count}, "
            f"mittlere Konfidenz: {_confidence_cell(result.average_confidence)}\n"
            f"Ausgewertet: {result.documents_scanned} Dokument(e), "
            f"{result.tables_used} von {result.tables_scanned} Tabelle(n) genutzt",
            title=f"Artikel {escape(result.tender_id)}",
        )
    )

    if shown:
        table = Table(title="Positionen", header_style="bold")
        table.add_column("Pos.", style="dim")
        table.add_column("Bezeichnung", overflow="fold")
        table.add_column("Menge", justify="right")
        table.add_column("Einheit")
        table.add_column("Hersteller / Typ", overflow="fold")
        table.add_column("Konf.", justify="right")
        for item in shown:
            brand = " / ".join(filter(None, (item.manufacturer, item.model_number)))
            if item.brand_locked:
                brand = f"[red]{escape(brand)}[/red]"
            elif brand:
                brand = escape(brand)
            table.add_row(
                _safe(item.position, missing="-"),
                _safe(item.title),
                _quantity_cell(item),
                escape(item.unit) if item.unit else "[dim]UNKNOWN[/dim]",
                brand or "[dim]-[/dim]",
                _confidence_cell(item.confidence),
            )
        console.print(table)
    else:
        console.print("[yellow]Keine Positionen erkannt.[/yellow]")

    if show_evidence:
        for item in shown:
            source = item.provenance.document if item.provenance else None
            page = item.provenance.page if item.provenance else None
            console.print(
                f"[dim]{_safe(item.position, missing='-')}[/dim] {_safe(source)}"
                + (f", Seite {page}" if page else "")
                + f"\n  {_safe(item.evidence())}"
            )

    for warning in result.warnings:
        console.print(f"[yellow]Hinweis[/yellow] {_safe(warning)}")
    for item in shown:
        for warning in item.warnings:
            console.print(
                f"[yellow]Hinweis[/yellow] {_safe(item.position, missing='-')}: {_safe(warning)}"
            )


def _money(amount: float | None, currency: str | None = None) -> str:
    """Betrag in deutscher Schreibweise; fehlender Betrag bleibt UNKNOWN."""
    if amount is None:
        return "[dim]UNKNOWN[/dim]"
    text = f"{amount:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{text} {escape(currency)}" if currency else text


@app.command()
def prices(
    tender_id: str | None = typer.Argument(
        None, help="Tender-ID oder Quell-ID; entfaellt bei --all"
    ),
    config: Path | None = typer.Option(None, "--config"),
    source: list[str] | None = typer.Option(
        None, "--source", "-s", help="nur diese Preisquelle(n) verwenden"
    ),
    min_confidence: int | None = typer.Option(
        None,
        "--min-confidence",
        help="Zuordnungsguete, ab der ein Angebot kalkulationsfaehig ist "
        "(Default: criteria.minimum_match_confidence)",
    ),
    research_all: bool = typer.Option(
        False, "--all", help="alle laufenden Ausschreibungen mit Positionen bepreisen"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="max. Ausschreibungen bei --all"),
    show_offers: bool = typer.Option(
        False, "--offers", help="alle Angebote je Position mit Begruendung anzeigen"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Preise zu den erkannten Positionen recherchieren (Stufe 4)."""
    settings = _settings(config)

    if research_all:
        report = asyncio.run(research_open_tenders(settings, limit=limit))
        if json_output:
            console.print_json(jsonlib.dumps(report.as_dict(), ensure_ascii=False, default=str))
            return
        table = Table(title=f"Preisrecherche ({report.count})", header_style="bold")
        table.add_column("Abdeckung", justify="right")
        table.add_column("kalkulierbar", justify="right")
        table.add_column("Positionen", justify="right")
        table.add_column("ID", style="dim", overflow="fold")
        for result in sorted(report.researched, key=lambda item: -item.coverage_percent):
            table.add_row(
                _coverage_cell(result.coverage_percent),
                str(result.usable_count),
                str(len(result.items)),
                escape(result.tender_id),
            )
        console.print(table)
        for failure in report.failed:
            console.print(
                f"[red]Preisrecherche fehlgeschlagen[/red] {escape(failure['tender_id'])}: "
                f"{escape(failure['error'])}"
            )
        return

    if not tender_id:
        console.print("[red]Bitte eine Tender-ID angeben oder --all verwenden.[/red]")
        raise typer.Exit(1)

    try:
        result = asyncio.run(
            research_and_store(
                settings,
                tender_id,
                only_sources=list(source) if source else None,
                minimum_confidence=min_confidence,
            )
        )
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        console.print_json(jsonlib.dumps(result.as_dict(), ensure_ascii=False, default=str))
        return

    threshold = (
        min_confidence if min_confidence is not None else settings.criteria.minimum_match_confidence
    )
    console.print(
        Panel.fit(
            f"Positionen: {len(result.items)}, kalkulationsfaehig: "
            f"[bold]{result.usable_count}[/bold] "
            f"({_coverage_cell(result.coverage_percent)} Abdeckung)\n"
            f"Quellen: {_safe(result.sources_used)} | "
            f"Zuordnungsguete ab {threshold} Punkten",
            title=f"Preise {escape(result.tender_id)}",
        )
    )

    if result.items:
        table = Table(title="Preisbild je Position", header_style="bold")
        table.add_column("Pos.", style="dim")
        table.add_column("Bezeichnung", overflow="fold")
        table.add_column("Menge", justify="right")
        table.add_column("Bester Preis", justify="right")
        table.add_column("Guete", justify="right")
        table.add_column("Angebote", justify="right")
        table.add_column("Spanne", justify="right")
        for item in result.items:
            best = item.best_match
            statistics = item.statistics
            usable = statistics.usable_count > 0
            net = best.quote.net_amount(item.quantity)[0] if best else None
            table.add_row(
                _safe(item.position, missing="-"),
                _safe(item.title),
                _safe(item.quantity, missing="-"),
                _money(net, statistics.currency) if usable else "[dim]UNKNOWN[/dim]",
                _confidence_cell(best.match_confidence) if best else "[dim]-[/dim]",
                str(statistics.offer_count),
                (
                    f"{_money(statistics.minimum)} - {_money(statistics.maximum)}"
                    if statistics.usable_count > 1
                    else "[dim]-[/dim]"
                ),
            )
        console.print(table)

    if show_offers:
        for item in result.items:
            if not item.matches:
                continue
            console.print(f"\n[bold]{_safe(item.position, missing='-')} {_safe(item.title)}[/bold]")
            offers = Table(header_style="bold", show_edge=False)
            offers.add_column("Guete", justify="right")
            offers.add_column("Lieferant")
            offers.add_column("Produkt", overflow="fold")
            offers.add_column("Preis", justify="right")
            offers.add_column("Basis")
            offers.add_column("Begruendung", overflow="fold")
            for match in item.matches:
                quote = match.quote
                # Begruendungen enthalten Fremdtext (Hersteller-, Produktnamen):
                # erst escapen, dann einfaerben - sonst faerbt eine Preisliste
                # die Ausgabe ein.
                notes = [escape(reason) for reason in match.reasons]
                notes += [f"[yellow]{escape(concern)}[/yellow]" for concern in match.concerns]
                offers.add_row(
                    _confidence_cell(match.match_confidence),
                    _safe(quote.supplier),
                    _safe(quote.product_name),
                    _money(quote.amount, quote.currency),
                    escape(str(quote.basis)),
                    "; ".join(notes),
                )
            console.print(offers)

    for warning in result.warnings:
        console.print(f"[yellow]Hinweis[/yellow] {_safe(warning)}")
    for item in result.items:
        for warning in item.warnings:
            console.print(
                f"[yellow]Hinweis[/yellow] {_safe(item.position, missing='-')}: {_safe(warning)}"
            )
    for failure in result.sources_failed:
        console.print(
            f"[red]Preisquelle gestoert[/red] {escape(failure['source'])}: "
            f"{escape(failure['error'])}"
        )


@app.command("cache-clear")
def cache_clear(config: Path | None = typer.Option(None, "--config")) -> None:
    """HTTP-Cache leeren."""
    settings = _settings(config)
    from .core.cache import ResponseCache

    cache = ResponseCache(settings.cache_dir, enabled=True)
    removed = cache.clear()
    console.print(f"{removed} zwischengespeicherte Antworten geloescht.")


def main() -> None:  # pragma: no cover - Einstiegspunkt
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
