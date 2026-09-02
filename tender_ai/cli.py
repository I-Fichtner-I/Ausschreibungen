"""Kommandozeile von tender-ai (Stufe 1: Recherche).

tender-ai init                 Verzeichnisse und Datenbank anlegen
tender-ai sources              konfigurierte Quellen anzeigen
tender-ai doctor               Quellen auf Erreichbarkeit pruefen
tender-ai search               Ausschreibungen recherchieren (live)
tender-ai list                 gespeicherte Ausschreibungen anzeigen
tender-ai show <id>            Details einer Ausschreibung
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
from .services import check_sources, run_search
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


def _tender_table(tenders: list[Tender], title: str = "Ausschreibungen") -> Table:
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("Quelle", style="cyan", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True, max_width=22)
    table.add_column("Titel", overflow="fold", max_width=60)
    table.add_column("Vergabestelle", overflow="fold", max_width=30)
    table.add_column("Frist", no_wrap=True)
    table.add_column("Tage", justify="right", no_wrap=True)
    table.add_column("Volumen", justify="right", no_wrap=True)
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
        stats = repository.stats()

    if json_output:
        console.print_json(
            jsonlib.dumps(
                {"stats": stats, "tenders": [t.model_dump(mode="json") for t in tenders]},
                ensure_ascii=False,
                default=str,
            )
        )
        return

    console.print(_tender_table(tenders, title=f"Gespeicherte Ausschreibungen ({len(tenders)})"))
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
