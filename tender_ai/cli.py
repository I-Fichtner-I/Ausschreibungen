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
from rich.panel import Panel
from rich.table import Table

from .config import Settings, load_settings
from .core.http import build_http_client
from .core.logging import configure_logging, get_logger
from .database.repository import TenderRepository
from .database.session import create_all, session_scope
from .export.exporters import EXPORT_FORMATS, export_tenders
from .models.common import display
from .models.tender import Tender
from .sources.base import SearchQuery
from .sources.registry import available_types, build_sources

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
        days_text = display(days_left)
        style = ""
        if isinstance(days_left, int):
            style = "red" if days_left < 3 else ("yellow" if days_left < 10 else "green")
        value = (
            f"{tender.estimated_value:,.0f} {tender.currency or ''}".strip()
            if tender.estimated_value is not None
            else "UNKNOWN"
        )
        table.add_row(
            tender.source,
            tender.source_id[:22],
            display(tender.title),
            display(tender.contracting_authority),
            display(tender.submission_deadline),
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
            source_report.name,
            source_report.type,
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
        where = error["source"]
        if error.get("tender_id"):
            where = f"{where}, Datensatz {error['tender_id']}"
        console.print(f"[red]Fehler in Quelle '{where}':[/red] {error['error']}")


# --- Kommandos ---------------------------------------------------------------
@app.command()
def init(config: Path | None = typer.Option(None, "--config", help="Pfad zu config.yaml")) -> None:
    """Verzeichnisse anlegen, Datenbankschema erzeugen, Konfiguration pruefen."""
    settings = _settings(config)
    create_all(settings.database_url)
    console.print(
        Panel.fit(
            f"Datenverzeichnis: {settings.data_dir}\n"
            f"Datenbank:        {settings.database_url}\n"
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
            name,
            source_config.type,
            "[green]ja[/green]" if source_config.enabled else "[dim]nein[/dim]",
            str(source_config.priority),
            display(source_config.requests_per_second or settings.http.requests_per_second),
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

    async def _run() -> list[dict[str, Any]]:
        http = build_http_client(settings.http, settings.cache_dir)
        try:
            configured = build_sources(settings, http, only=source, include_disabled=True)
            if not configured:
                return []
            statuses = await asyncio.gather(*(s.health_check() for s in configured))
            return [status.as_dict() for status in statuses]
        finally:
            await http.aclose()

    results = asyncio.run(_run())
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
            result["name"],
            result["type"],
            "[green]OK[/green]" if result["ok"] else "[red]FEHLER[/red]",
            result["message"],
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

    async def _run(session: Any) -> Any:
        http = build_http_client(settings.http, settings.cache_dir)
        try:
            configured = build_sources(settings, http, only=source)
            if not configured:
                console.print(
                    "[red]Keine aktive Quelle gefunden.[/red] Pruefe config.yaml "
                    "(sources.<name>.enabled) oder --source."
                )
                raise typer.Exit(1)
            from .pipeline.ingest import IngestService

            service = IngestService(settings, configured, http, session=session)
            return await service.run(query, store=store, download_documents=download_docs)
        finally:
            await http.aclose()

    if store:
        with session_scope(settings.database_url) as session:
            report = asyncio.run(_run(session))
    else:
        report = asyncio.run(_run(None))

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
        changes = [
            change for change in repository.recent_changes(200) if change.tender_id == record.id
        ]

    if json_output:
        console.print_json(
            jsonlib.dumps(tender.model_dump(mode="json"), ensure_ascii=False, default=str)
        )
        return

    body = "\n".join(
        [
            f"[bold]{display(tender.title)}[/bold]",
            "",
            f"Vergabestelle:  {display(tender.contracting_authority)}",
            f"Quelle:         {tender.source} ({display(tender.source_url)})",
            f"Amtliche Nr.:   {display(tender.national_id)}",
            f"Land/Region:    {display(tender.country)} / {display(tender.region)}",
            f"CPV:            {display(tender.cpv_codes)}",
            f"Veroeffentlicht:{display(tender.publication_date)}",
            (
                f"Angebotsfrist:  {display(tender.submission_deadline)} "
                f"(in {display(tender.days_until_deadline)} Tagen)"
            ),
            f"Bindefrist:     {display(tender.binding_period_end)}",
            f"Lieferfrist:    {display(tender.delivery_deadline)}",
            (
                f"Volumen:        {display(tender.estimated_value)} "
                f"{display(tender.currency, missing='')}"
            ),
            f"Verfahren:      {display(tender.procedure_type)}",
            f"Status:         {tender.status}",
            "",
            f"{display(tender.description)}",
        ]
    )
    console.print(Panel(body, title=tender.id, expand=True))

    if tender.lots:
        table = Table(title="Lose", header_style="bold")
        table.add_column("Los")
        table.add_column("Titel", overflow="fold")
        table.add_column("Volumen", justify="right")
        for lot in tender.lots:
            table.add_row(display(lot.lot_id), display(lot.title), display(lot.estimated_value))
        console.print(table)

    if tender.documents:
        table = Table(title="Dokumente", header_style="bold")
        table.add_column("Name", overflow="fold")
        table.add_column("Zugriff")
        table.add_column("URL", overflow="fold")
        table.add_column("Lokal", overflow="fold")
        for document in tender.documents:
            table.add_row(
                display(document.name),
                str(document.access),
                display(document.url),
                display(document.local_path),
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
                display(alias.match_reason),
                display(alias.match_confidence),
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
                display(change.detected_at),
                change.field,
                display(change.old_value),
                display(change.new_value),
            )
        console.print(table)

    if tender.notes:
        console.print(Panel("\n".join(f"- {note}" for note in tender.notes), title="Hinweise"))


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
            display(run.started_at),
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
                state.name,
                display(state.last_run_at),
                display(state.last_success_at),
                str(state.last_result_count),
                str(state.consecutive_failures),
                display(state.last_error, missing="-"),
            )
        console.print(state_table)


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
