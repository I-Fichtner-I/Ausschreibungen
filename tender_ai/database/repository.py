"""Repository: Ausschreibungen speichern, aktualisieren, abfragen.

Kernstueck ist ``upsert``: es unterscheidet neu / aktualisiert / unveraendert /
Dublette, protokolliert Aenderungen feldweise (Basis der Ueberwachung) und
haelt die Primaerquelle nach Quellprioritaet aktuell.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DedupConfig
from ..models.common import normalize_text, utcnow
from ..models.tender import Tender, TenderStatus
from ..pipeline.dedup import DuplicateDetector, DuplicateMatch
from .models import (
    IngestRunRecord,
    SourceStateRecord,
    TenderAliasRecord,
    TenderChangeRecord,
    TenderDocumentRecord,
    TenderRecord,
)

#: Felder, deren Aenderung eine erneute Analyse rechtfertigt.
WATCHED_FIELDS = (
    "title",
    "submission_deadline",
    "status",
    "estimated_value",
    "currency",
    "cpv_codes",
    "description",
)


@dataclass(slots=True)
class UpsertResult:
    action: str  # "new" | "updated" | "unchanged" | "duplicate"
    record: TenderRecord
    changes: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    duplicate_of: str | None = None
    duplicate_reason: str | None = None
    duplicate_confidence: int | None = None


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


class TenderRepository:
    def __init__(
        self,
        session: Session,
        dedup_config: DedupConfig | None = None,
        source_priority: dict[str, int] | None = None,
    ) -> None:
        self.session = session
        self.detector = DuplicateDetector(dedup_config or DedupConfig())
        self.source_priority = source_priority or {}

    # --- Schreiben ---------------------------------------------------------
    def upsert(self, tender: Tender) -> UpsertResult:
        existing = self.session.get(TenderRecord, tender.id)
        if existing is not None:
            return self._update_existing(existing, tender)

        duplicate = self.detector.find(self.session, tender)
        record = self._create_record(tender)
        self.session.add(record)
        self.session.flush()

        if duplicate is None:
            return UpsertResult(action="new", record=record)

        self._link_duplicate(record, duplicate)
        return UpsertResult(
            action="duplicate",
            record=record,
            duplicate_of=duplicate.record.id,
            duplicate_reason=duplicate.reason,
            duplicate_confidence=duplicate.confidence,
        )

    def _create_record(self, tender: Tender) -> TenderRecord:
        record = TenderRecord(
            id=tender.id,
            fingerprint=tender.fingerprint(),
            source=tender.source,
            source_id=tender.source_id,
            source_url=tender.source_url,
            national_id=tender.national_id,
            title=tender.title,
            title_normalized=normalize_text(tender.title),
            contracting_authority=tender.contracting_authority,
            authority_normalized=normalize_text(tender.contracting_authority),
            description=tender.description,
            country=tender.country,
            region=tender.region,
            cpv_codes=list(tender.cpv_codes),
            notice_type=tender.notice_type,
            procedure_type=tender.procedure_type,
            status=str(tender.status),
            publication_date=tender.publication_date,
            submission_deadline=tender.submission_deadline,
            estimated_value=tender.estimated_value,
            currency=tender.currency,
            payload=_json_ready(tender.model_dump(mode="json")),
            content_hash=tender.content_hash(),
            is_primary=True,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
        record.documents = [
            TenderDocumentRecord(
                name=doc.name,
                url=doc.url,
                media_type=doc.media_type,
                access=str(doc.access),
                local_path=doc.local_path,
                checksum_sha256=doc.checksum_sha256,
                retrieved_at=doc.retrieved_at,
            )
            for doc in tender.documents
        ]
        return record

    def _update_existing(self, record: TenderRecord, tender: Tender) -> UpsertResult:
        record.last_seen_at = utcnow()
        new_hash = tender.content_hash()
        if record.content_hash == new_hash:
            return UpsertResult(action="unchanged", record=record)

        changes: list[tuple[str, str | None, str | None]] = []
        for field_name in WATCHED_FIELDS:
            old_value = getattr(record, field_name, None)
            new_value = getattr(tender, field_name, None)
            if field_name == "status":
                new_value = str(new_value)
            if field_name == "description":
                # Nur die Tatsache der Aenderung protokollieren, nicht den ganzen Text
                if _as_text(old_value) != _as_text(new_value):
                    changes.append((field_name, "(geaendert)", "(geaendert)"))
                continue
            if _as_text(old_value) != _as_text(new_value):
                changes.append((field_name, _as_text(old_value), _as_text(new_value)))

        old_doc_urls = {doc.url for doc in record.documents}
        new_doc_urls = {doc.url for doc in tender.documents}
        if old_doc_urls != new_doc_urls:
            changes.append(("documents", str(len(old_doc_urls)), str(len(new_doc_urls))))

        # Felder uebernehmen
        record.title = tender.title
        record.title_normalized = normalize_text(tender.title)
        record.contracting_authority = tender.contracting_authority
        record.authority_normalized = normalize_text(tender.contracting_authority)
        record.description = tender.description
        record.country = tender.country
        record.region = tender.region
        record.cpv_codes = list(tender.cpv_codes)
        record.notice_type = tender.notice_type
        record.procedure_type = tender.procedure_type
        record.status = str(tender.status)
        record.publication_date = tender.publication_date
        record.submission_deadline = tender.submission_deadline
        record.estimated_value = tender.estimated_value
        record.currency = tender.currency
        record.source_url = tender.source_url
        record.national_id = tender.national_id or record.national_id
        record.fingerprint = tender.fingerprint()
        record.payload = _json_ready(tender.model_dump(mode="json"))
        record.content_hash = new_hash

        if old_doc_urls != new_doc_urls:
            record.documents = [
                TenderDocumentRecord(
                    name=doc.name,
                    url=doc.url,
                    media_type=doc.media_type,
                    access=str(doc.access),
                    local_path=doc.local_path,
                    checksum_sha256=doc.checksum_sha256,
                    retrieved_at=doc.retrieved_at,
                )
                for doc in tender.documents
            ]

        for field_name, old_value, new_value in changes:
            self.session.add(
                TenderChangeRecord(
                    tender_id=record.id,
                    field=field_name,
                    old_value=old_value,
                    new_value=new_value,
                    source=tender.source,
                )
            )
        self.session.flush()
        return UpsertResult(action="updated", record=record, changes=changes)

    def _link_duplicate(self, record: TenderRecord, duplicate: DuplicateMatch) -> None:
        """Neuen Fund mit dem bestehenden Datensatz verknuepfen.

        Primaerquelle ist die Quelle mit der besten (niedrigsten) Prioritaet.
        """
        primary = duplicate.record
        if primary.primary_tender_id and primary.primary_tender_id != primary.id:
            resolved = self.session.get(TenderRecord, primary.primary_tender_id)
            if resolved is not None:
                primary = resolved

        new_priority = self.source_priority.get(record.source, 50)
        old_priority = self.source_priority.get(primary.source, 50)

        if new_priority < old_priority:
            # Neuer Datensatz wird Primaerquelle; bestehende Kinder umhaengen.
            for child in self.session.scalars(
                select(TenderRecord).where(TenderRecord.primary_tender_id == primary.id)
            ):
                child.primary_tender_id = record.id
            primary.is_primary = False
            primary.primary_tender_id = record.id
            record.is_primary = True
            record.primary_tender_id = record.id
            target, alias_of = record, primary
        else:
            record.is_primary = False
            record.primary_tender_id = primary.id
            target, alias_of = primary, record

        self.session.add(
            TenderAliasRecord(
                tender_id=target.id,
                source=alias_of.source,
                source_id=alias_of.source_id,
                source_url=alias_of.source_url,
                match_reason=duplicate.reason,
                match_confidence=duplicate.confidence,
            )
        )
        self.session.flush()

    # --- Lesen -------------------------------------------------------------
    def get(self, tender_id: str) -> TenderRecord | None:
        record = self.session.get(TenderRecord, tender_id)
        if record is not None:
            return record
        # Kurzform erlauben: nur die Quell-ID
        return self.session.scalars(
            select(TenderRecord).where(TenderRecord.source_id == tender_id).limit(1)
        ).first()

    def list_tenders(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sources: Sequence[str] | None = None,
        status: str | None = None,
        search: str | None = None,
        only_primary: bool = True,
        open_only: bool = False,
        min_days_until_deadline: int | None = None,
        order_by: str = "deadline",
    ) -> list[TenderRecord]:
        stmt = select(TenderRecord)
        if only_primary:
            stmt = stmt.where(TenderRecord.is_primary.is_(True))
        if sources:
            stmt = stmt.where(TenderRecord.source.in_(list(sources)))
        if status:
            stmt = stmt.where(TenderRecord.status == status)
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(
                func.lower(TenderRecord.title).like(pattern)
                | func.lower(TenderRecord.contracting_authority).like(pattern)
            )
        if open_only:
            stmt = stmt.where(
                TenderRecord.submission_deadline.is_(None)
                | (TenderRecord.submission_deadline >= datetime.now(UTC))
            )
        if min_days_until_deadline is not None:
            threshold = datetime.now(UTC) + timedelta(days=min_days_until_deadline)
            stmt = stmt.where(
                TenderRecord.submission_deadline.is_(None)
                | (TenderRecord.submission_deadline >= threshold)
            )

        if order_by == "deadline":
            stmt = stmt.order_by(
                TenderRecord.submission_deadline.is_(None), TenderRecord.submission_deadline.asc()
            )
        elif order_by == "published":
            stmt = stmt.order_by(TenderRecord.publication_date.desc().nulls_last())
        elif order_by == "value":
            stmt = stmt.order_by(TenderRecord.estimated_value.desc().nulls_last())
        else:
            stmt = stmt.order_by(TenderRecord.last_seen_at.desc())

        return list(self.session.scalars(stmt.offset(offset).limit(limit)))

    def count(self, only_primary: bool = True) -> int:
        stmt = select(func.count()).select_from(TenderRecord)
        if only_primary:
            stmt = stmt.where(TenderRecord.is_primary.is_(True))
        return int(self.session.scalar(stmt) or 0)

    def counts_by_source(self) -> dict[str, int]:
        stmt = select(TenderRecord.source, func.count()).group_by(TenderRecord.source)
        return {source: int(count) for source, count in self.session.execute(stmt)}

    def recent_changes(self, limit: int = 50) -> list[TenderChangeRecord]:
        return list(
            self.session.scalars(
                select(TenderChangeRecord)
                .order_by(TenderChangeRecord.detected_at.desc())
                .limit(limit)
            )
        )

    def aliases_for(self, tender_id: str) -> list[TenderAliasRecord]:
        return list(
            self.session.scalars(
                select(TenderAliasRecord).where(TenderAliasRecord.tender_id == tender_id)
            )
        )

    @staticmethod
    def to_tender(record: TenderRecord) -> Tender:
        """DB-Datensatz zurueck in das Pydantic-Modell wandeln."""
        payload = dict(record.payload or {})
        if not payload:
            payload = {
                "id": record.id,
                "source": record.source,
                "source_id": record.source_id,
                "title": record.title,
            }
        try:
            return Tender.model_validate(payload)
        except Exception:  # noqa: BLE001 - ein defekter Payload darf die Anzeige nicht verhindern
            return Tender(
                id=record.id,
                source=record.source,
                source_id=record.source_id,
                source_url=record.source_url,
                title=record.title,
                contracting_authority=record.contracting_authority,
                status=TenderStatus(record.status)
                if record.status in TenderStatus.__members__.values()
                else TenderStatus.UNKNOWN,
            )

    # --- Laufprotokolle ----------------------------------------------------
    def start_run(self, sources: Iterable[str], query: dict[str, Any]) -> IngestRunRecord:
        run = IngestRunRecord(sources=list(sources), query=_json_ready(query))
        self.session.add(run)
        self.session.flush()
        return run

    def finish_run(
        self,
        run: IngestRunRecord,
        *,
        found: int,
        new: int,
        updated: int,
        duplicates: int,
        errors: list[dict[str, Any]],
        http_stats: dict[str, Any],
    ) -> IngestRunRecord:
        run.finished_at = utcnow()
        run.found = found
        run.new = new
        run.updated = updated
        run.duplicates = duplicates
        run.errors = _json_ready(errors)
        run.http_stats = _json_ready(http_stats)
        self.session.flush()
        return run

    def last_runs(self, limit: int = 10) -> list[IngestRunRecord]:
        return list(
            self.session.scalars(
                select(IngestRunRecord).order_by(IngestRunRecord.started_at.desc()).limit(limit)
            )
        )

    def update_source_state(
        self,
        name: str,
        source_type: str,
        *,
        success: bool,
        result_count: int = 0,
        error: str | None = None,
    ) -> SourceStateRecord:
        state = self.session.get(SourceStateRecord, name)
        if state is None:
            # Zaehler explizit setzen: Spalten-Defaults greifen erst beim INSERT.
            state = SourceStateRecord(
                name=name, type=source_type, consecutive_failures=0, last_result_count=0
            )
            self.session.add(state)
        state.type = source_type
        state.last_run_at = utcnow()
        state.last_result_count = result_count
        if success:
            state.last_success_at = utcnow()
            state.last_error = None
            state.consecutive_failures = 0
        else:
            state.last_error = error
            state.consecutive_failures = (state.consecutive_failures or 0) + 1
        self.session.flush()
        return state

    def source_states(self) -> list[SourceStateRecord]:
        return list(self.session.scalars(select(SourceStateRecord)))

    def stats(self) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        open_stmt = (
            select(func.count())
            .select_from(TenderRecord)
            .where(
                TenderRecord.is_primary.is_(True),
                TenderRecord.submission_deadline.is_(None)
                | (TenderRecord.submission_deadline >= datetime.now(UTC)),
            )
        )
        return {
            "tenders_total": self.count(only_primary=False),
            "tenders_primary": self.count(only_primary=True),
            "tenders_open": int(self.session.scalar(open_stmt) or 0),
            "by_source": self.counts_by_source(),
            "as_of": today.isoformat(),
        }
