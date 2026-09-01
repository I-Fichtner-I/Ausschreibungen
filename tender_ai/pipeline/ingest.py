"""Rechercherlauf: Quellen abfragen, Ergebnisse vereinheitlichen und speichern.

Robustheitsregel: Der Ausfall einer Quelle beendet nie den Gesamtlauf. Jede
Quelle wird einzeln gekapselt, Fehler werden protokolliert und im Bericht
ausgewiesen. Die Suche laeuft parallel, die Persistenz sequenziell in
Prioritaetsreihenfolge - so wird bei Dubletten zuverlaessig die hoeher
priorisierte Quelle zur Primaerquelle.

Persistenz je Datensatz in einem Savepoint: ein fehlerhafter Datensatz kostet
genau diesen Datensatz, nie die bereits gespeicherten derselben Quelle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..core.http import HttpClient
from ..core.logging import get_logger
from ..database.repository import TenderRepository, UpsertResult
from ..models.tender import Tender
from ..sources.base import SearchQuery, TenderSource

log = get_logger(__name__)


@dataclass(slots=True)
class SourceReport:
    name: str
    type: str
    ok: bool = True
    found: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "type": self.type,
            "ok": self.ok,
            "found": self.found,
            "new": self.new,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "duplicates": self.duplicates,
            "failed": self.failed,
            "failed_ids": list(self.failed_ids),
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 2),
        }


@dataclass(slots=True)
class IngestReport:
    sources: list[SourceReport] = field(default_factory=list)
    tenders: list[Tender] = field(default_factory=list)
    new_tender_ids: list[str] = field(default_factory=list)
    updated_tender_ids: list[str] = field(default_factory=list)
    #: Fehler einzelner Datensaetze: {"source", "tender_id", "error"}
    record_errors: list[dict[str, Any]] = field(default_factory=list)
    http_stats: dict[str, Any] = field(default_factory=dict)
    stored: bool = True

    @property
    def found(self) -> int:
        return sum(report.found for report in self.sources)

    @property
    def new(self) -> int:
        return sum(report.new for report in self.sources)

    @property
    def updated(self) -> int:
        return sum(report.updated for report in self.sources)

    @property
    def duplicates(self) -> int:
        return sum(report.duplicates for report in self.sources)

    @property
    def failed(self) -> int:
        return sum(report.failed for report in self.sources)

    @property
    def errors(self) -> list[dict[str, Any]]:
        """Quellfehler und Datensatzfehler gemeinsam - fuer Laufprotokoll und CLI."""
        source_errors = [
            {"source": report.name, "error": report.error}
            for report in self.sources
            if not report.ok
        ]
        return source_errors + list(self.record_errors)

    @property
    def source_errors(self) -> list[dict[str, Any]]:
        return [
            {"source": report.name, "error": report.error}
            for report in self.sources
            if not report.ok
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "new": self.new,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "failed": self.failed,
            "stored": self.stored,
            "sources": [report.as_dict() for report in self.sources],
            "errors": self.errors,
            "http": self.http_stats,
        }


class IngestService:
    def __init__(
        self,
        settings: Settings,
        sources: Sequence[TenderSource],
        http: HttpClient,
        session: Session | None = None,
    ) -> None:
        self.settings = settings
        self.sources = list(sources)
        self.http = http
        self.session = session
        self.repository = (
            TenderRepository(
                session,
                dedup_config=settings.dedup,
                source_priority={name: cfg.priority for name, cfg in settings.sources.items()},
            )
            if session is not None
            else None
        )

    async def _search_one(
        self, source: TenderSource, query: SearchQuery
    ) -> tuple[TenderSource, list[Tender] | Exception, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            results = await source.search(query)
        except Exception as exc:  # noqa: BLE001 - eine Quelle darf nie den Gesamtlauf beenden
            log.error("source_failed", source=source.name, error=str(exc))
            return source, exc, loop.time() - started
        return source, results, loop.time() - started

    def _persist_one(self, tender: Tender) -> UpsertResult:
        """Einen Datensatz in einem Savepoint speichern.

        Bei einer Exception rollt SQLAlchemy nur den Savepoint zurueck; die
        vorher geflushten Datensaetze derselben Quelle bleiben erhalten.
        """
        assert self.repository is not None and self.session is not None
        with self.session.begin_nested():
            return self.repository.upsert(tender)

    def _persist_source(
        self, source: TenderSource, tenders: Sequence[Tender], report: IngestReport
    ) -> SourceReport:
        source_report = SourceReport(name=source.name, type=source.type_name, found=len(tenders))
        assert self.session is not None
        for tender in tenders:
            try:
                result = self._persist_one(tender)
            except Exception as exc:  # noqa: BLE001 - ein defekter Datensatz kostet nur sich selbst
                log.error("persist_failed", tender=tender.id, error=str(exc))
                source_report.failed += 1
                source_report.failed_ids.append(tender.id)
                report.record_errors.append(
                    {"source": source.name, "tender_id": tender.id, "error": str(exc)}
                )
                continue

            # Zaehler erst nach erfolgreichem Savepoint erhoehen.
            if result.action == "new":
                source_report.new += 1
                report.new_tender_ids.append(result.record.id)
            elif result.action == "updated":
                source_report.updated += 1
                report.updated_tender_ids.append(result.record.id)
                log.info(
                    "tender_changed",
                    tender=result.record.id,
                    changes=[change[0] for change in result.changes],
                )
            elif result.action == "duplicate":
                source_report.duplicates += 1
                log.info(
                    "duplicate_detected",
                    tender=result.record.id,
                    duplicate_of=result.duplicate_of,
                    reason=result.duplicate_reason,
                    confidence=result.duplicate_confidence,
                )
            else:
                source_report.unchanged += 1
        self.session.commit()
        return source_report

    async def run(
        self,
        query: SearchQuery,
        *,
        store: bool = True,
        download_documents: bool = False,
    ) -> IngestReport:
        report = IngestReport(stored=store and self.repository is not None)
        if not self.sources:
            log.warning("no_sources_enabled")
            return report

        run_record = None
        if self.repository is not None and store:
            run_record = self.repository.start_run(
                [source.name for source in self.sources],
                {
                    "keywords": query.keywords,
                    "cpv_codes": query.cpv_codes,
                    "countries": query.countries,
                    "published_after": str(query.published_after),
                    "max_results": query.max_results,
                },
            )

        results = await asyncio.gather(
            *(self._search_one(source, query) for source in self.sources)
        )

        # Persistenz sequenziell in Prioritaetsreihenfolge
        for source, outcome, duration in sorted(results, key=lambda item: item[0].priority):
            if isinstance(outcome, Exception):
                source_report = SourceReport(
                    name=source.name,
                    type=source.type_name,
                    ok=False,
                    error=f"{type(outcome).__name__}: {outcome}",
                    duration_seconds=duration,
                )
                if self.repository is not None:
                    self.repository.update_source_state(
                        source.name, source.type_name, success=False, error=source_report.error
                    )
                report.sources.append(source_report)
                continue

            tenders = outcome
            report.tenders.extend(tenders)

            if download_documents:
                await self._download_documents(source, tenders)

            if self.repository is not None and store:
                source_report = self._persist_source(source, tenders, report)
            else:
                source_report = SourceReport(
                    name=source.name, type=source.type_name, found=len(tenders)
                )
            source_report.duration_seconds = duration

            if self.repository is not None:
                self.repository.update_source_state(
                    source.name, source.type_name, success=True, result_count=len(tenders)
                )
            report.sources.append(source_report)

        report.http_stats = self.http.stats.as_dict()

        if self.repository is not None and run_record is not None and self.session is not None:
            self.repository.finish_run(
                run_record,
                found=report.found,
                new=report.new,
                updated=report.updated,
                duplicates=report.duplicates,
                errors=report.errors,
                http_stats=report.http_stats,
            )
            self.session.commit()

        log.info(
            "ingest_done",
            found=report.found,
            new=report.new,
            updated=report.updated,
            duplicates=report.duplicates,
            failed=report.failed,
            errors=len(report.source_errors),
        )
        return report

    async def _download_documents(self, source: TenderSource, tenders: Sequence[Tender]) -> None:
        destination = Path(self.settings.documents_dir)
        for tender in tenders:
            try:
                await source.download_documents(tender, destination)
            except Exception as exc:  # noqa: BLE001 - Downloadfehler duerfen die Recherche nicht stoppen
                log.warning(
                    "documents_failed", source=source.name, tender=tender.id, error=str(exc)
                )
