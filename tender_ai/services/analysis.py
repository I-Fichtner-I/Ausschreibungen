"""Stufe 2B: Ausschreibung analysieren - Anforderungen und Risiko.

Baut auf den in Stufe 2A ausgelesenen Unterlagen auf: aus deren Text werden
Hinweise erkannt, in ``TenderRequirements`` ueberfuehrt und zu einem
begruendeten Risiko-Score verdichtet. Beides wird gespeichert, damit die
Bewertung spaeter nachvollziehbar bleibt und nicht bei jedem Aufruf neu
entsteht.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..analysis import (
    assess_risk,
    equivalence_scope,
    extract_requirements,
    findings_to_requirements,
)
from ..config import Settings
from ..core.errors import ConfigError
from ..core.logging import get_logger
from ..database.repository import TenderRepository
from ..database.session import session_scope
from ..models.analysis import AnalysisResult, RequirementKind
from .documents import documents_from_db, fetch_documents

log = get_logger(__name__)


async def analyze_tender(
    settings: Settings,
    tender_id: str,
    *,
    fetch_missing: bool = True,
) -> AnalysisResult:
    """Unterlagen auswerten, Anforderungen erkennen, Risiko bewerten.

    ``fetch_missing`` laedt fehlende Unterlagen vorher nach - so genuegt ein
    Befehl vom Suchtreffer bis zur Bewertung.
    """
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(tender_id)
        if record is None:
            raise ConfigError(f"Ausschreibung nicht gefunden: {tender_id}")
        resolved_id = record.id
        needs_fetch = fetch_missing and not repository.extracts_for(resolved_id)

    if needs_fetch:
        await fetch_documents(settings, resolved_id)

    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(resolved_id)
        if record is None:  # pragma: no cover - zwischenzeitlich geloescht
            raise ConfigError(f"Ausschreibung nicht gefunden: {tender_id}")

        tender = TenderRepository.to_tender(record)
        documents = documents_from_db(repository, resolved_id)

        findings = extract_requirements(documents)
        tender.requirements = findings_to_requirements(findings)
        brand_findings = [
            finding for finding in findings if finding.kind is RequirementKind.BRAND_LOCK
        ]
        risk = assess_risk(
            tender,
            findings,
            documents,
            criteria=settings.criteria,
            equivalence_scope=equivalence_scope(documents, brand_findings),
        )
        result = AnalysisResult(tender_id=resolved_id, findings=findings, risk=risk)

        repository.save_requirements(record, tender)
        repository.save_risk(result, record)
        session.commit()

        log.info(
            "tender_analyzed",
            tender=resolved_id,
            findings=len(findings),
            risk_score=risk.score,
            risk_level=str(risk.level),
            documents=risk.documents_analyzed,
        )
        return result


@dataclass(slots=True)
class BatchAnalysisReport:
    """Ergebnis einer Stapelanalyse - fuer den taeglichen Lauf."""

    analyzed: list[AnalysisResult] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.analyzed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzed": self.count,
            "failed": self.failed,
            "results": [
                {
                    "tender_id": result.tender_id,
                    "score": result.risk.score,
                    "level": str(result.risk.level),
                    "findings": len(result.findings),
                }
                for result in sorted(self.analyzed, key=lambda item: item.risk.score, reverse=True)
            ],
        }


async def analyze_open_tenders(
    settings: Settings,
    *,
    limit: int = 50,
    fetch_missing: bool = True,
    skip_analyzed: bool = True,
) -> BatchAnalysisReport:
    """Alle laufenden Ausschreibungen analysieren.

    ``skip_analyzed`` ueberspringt Ausschreibungen, die seit ihrer letzten
    Aenderung bereits bewertet wurden - der taegliche Lauf soll nicht jedes Mal
    alle Unterlagen neu auswerten. Ein Fehler bei einer Ausschreibung stoppt
    den Stapel nicht.
    """
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        candidates: list[str] = []
        for record in repository.list_tenders(limit=limit, open_only=True, order_by="deadline"):
            # Nur neu bewerten, wenn sich der Inhalt der Ausschreibung seit der
            # letzten Bewertung geaendert hat. ``updated_at`` taugt dafuer nicht:
            # die Analyse schreibt die erkannten Anforderungen zurueck und wuerde
            # damit ihre eigene Neubewertung ausloesen.
            already_current = (
                skip_analyzed
                and record.risk_analysis is not None
                and record.risk_analysis.content_hash == record.content_hash
            )
            if already_current:
                continue
            candidates.append(record.id)

    report = BatchAnalysisReport()
    for tender_id in candidates:
        try:
            report.analyzed.append(
                await analyze_tender(settings, tender_id, fetch_missing=fetch_missing)
            )
        except Exception as exc:  # noqa: BLE001 - eine Ausschreibung stoppt nie den Stapel
            log.error("analysis_failed", tender=tender_id, error=str(exc))
            report.failed.append({"tender_id": tender_id, "error": str(exc)})
    log.info("batch_analysis_done", analyzed=report.count, failed=len(report.failed))
    return report


def analyze_tender_sync(settings: Settings, tender_id: str, **kwargs: Any) -> AnalysisResult:
    """Synchroner Einstieg fuer CLI und Skripte."""
    return asyncio.run(analyze_tender(settings, tender_id, **kwargs))
