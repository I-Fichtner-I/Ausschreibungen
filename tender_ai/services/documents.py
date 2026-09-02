"""Stufe 2: Vergabeunterlagen beschaffen und auslesen.

Ablauf je Ausschreibung: frei zugaengliche Dokumente herunterladen, Text und
Tabellen extrahieren, Ergebnis speichern. Geschuetzte Dokumente (Login,
Captcha, Paywall) werden uebersprungen und bleiben mit ihrem ``access``-Status
sichtbar - sie werden nicht umgangen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..core.errors import ConfigError
from ..core.http import build_http_client
from ..core.logging import get_logger
from ..database.repository import TenderRepository
from ..database.session import session_scope
from ..extraction import extract_document
from ..models.document import ExtractedDocument, ExtractedPage, ExtractedTable, ExtractionStatus
from ..models.tender import DocumentAccess, Tender
from ..sources.registry import build_sources

log = get_logger(__name__)


@dataclass(slots=True)
class DocumentResult:
    name: str | None
    url: str | None
    access: str
    downloaded: bool = False
    local_path: str | None = None
    extractor: str | None = None
    status: str | None = None
    page_count: int = 0
    table_count: int = 0
    character_count: int = 0
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "access": self.access,
            "downloaded": self.downloaded,
            "local_path": self.local_path,
            "extractor": self.extractor,
            "status": self.status,
            "page_count": self.page_count,
            "table_count": self.table_count,
            "character_count": self.character_count,
            "note": self.note,
        }


@dataclass(slots=True)
class DocumentReport:
    tender_id: str
    title: str | None = None
    documents: list[DocumentResult] = field(default_factory=list)
    skipped_restricted: int = 0

    @property
    def downloaded(self) -> int:
        return sum(1 for doc in self.documents if doc.downloaded)

    @property
    def extracted(self) -> int:
        return sum(1 for doc in self.documents if doc.status == str(ExtractionStatus.OK))

    @property
    def failed(self) -> int:
        return sum(
            1
            for doc in self.documents
            if doc.status in (str(ExtractionStatus.FAILED), str(ExtractionStatus.UNSUPPORTED))
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "title": self.title,
            "downloaded": self.downloaded,
            "extracted": self.extracted,
            "failed": self.failed,
            "skipped_restricted": self.skipped_restricted,
            "documents": [doc.as_dict() for doc in self.documents],
        }


async def fetch_documents(
    settings: Settings,
    tender_id: str,
    *,
    extract: bool = True,
    force: bool = False,
) -> DocumentReport:
    """Unterlagen einer gespeicherten Ausschreibung laden und auslesen.

    ``force`` laedt auch Dokumente erneut, die lokal bereits vorliegen.
    """
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        record = repository.get(tender_id)
        if record is None:
            raise ConfigError(f"Ausschreibung nicht gefunden: {tender_id}")

        tender = TenderRepository.to_tender(record)
        report = DocumentReport(tender_id=record.id, title=record.title)
        document_records = repository.documents_for(record.id)

        pending = _pending(tender, document_records, force=force)
        report.skipped_restricted = sum(
            1 for doc in tender.documents if doc.access is not DocumentAccess.PUBLIC
        )

        if pending:
            await _download(settings, tender, pending, report, repository)
        for document, document_record in zip(tender.documents, document_records, strict=False):
            if extract and document.local_path:
                _extract_one(settings, document, document_record, report, repository)
        session.commit()
        return report


def _pending(tender: Tender, records: Sequence[Any], *, force: bool) -> list[int]:
    """Indizes der Dokumente, die (erneut) geladen werden muessen."""
    pending: list[int] = []
    for index, document in enumerate(tender.documents):
        if document.access is not DocumentAccess.PUBLIC or not document.url:
            continue
        existing = records[index].local_path if index < len(records) else None
        if not force and existing and Path(existing).is_file():
            document.local_path = existing
            continue
        pending.append(index)
    return pending


async def _download(
    settings: Settings,
    tender: Tender,
    pending: Sequence[int],
    report: DocumentReport,
    repository: TenderRepository,
) -> None:
    http = build_http_client(settings.http, settings.cache_dir)
    try:
        sources = build_sources(settings, http, only=[tender.source], include_disabled=True)
        if not sources:
            raise ConfigError(
                f"Quelle '{tender.source}' ist nicht konfiguriert - "
                "die Unterlagen koennen nicht geladen werden."
            )
        # Nur die noch fehlenden Dokumente anbieten, damit vorhandene Dateien
        # nicht erneut uebertragen werden.
        subset = Tender(**{**tender.model_dump(), "documents": []})
        subset.documents = [tender.documents[index] for index in pending]
        await sources[0].download_documents(subset, Path(settings.documents_dir))
    finally:
        await http.aclose()

    records = repository.documents_for(tender.id)
    for document, record in zip(tender.documents, records, strict=False):
        repository.update_document(record, document)
    _ = report


def _extract_one(
    settings: Settings,
    document: Any,
    document_record: Any,
    report: DocumentReport,
    repository: TenderRepository,
) -> None:
    result = DocumentResult(
        name=document.name,
        url=document.url,
        access=str(document.access),
        downloaded=bool(document.local_path),
        local_path=document.local_path,
        note=document.note,
    )
    extracted = extract_document(document.local_path, document.media_type)
    result.extractor = extracted.extractor
    result.status = str(extracted.status)
    result.page_count = extracted.page_count
    result.table_count = len(extracted.tables)
    result.character_count = extracted.character_count
    repository.save_extract(document_record, extracted)
    log.info(
        "document_extracted",
        tender=report.tender_id,
        file=document.local_path,
        status=str(extracted.status),
        pages=extracted.page_count,
        tables=len(extracted.tables),
    )
    report.documents.append(result)


def fetch_documents_sync(settings: Settings, tender_id: str, **kwargs: Any) -> DocumentReport:
    """Synchroner Einstieg fuer CLI und Skripte."""
    return asyncio.run(fetch_documents(settings, tender_id, **kwargs))


def documents_from_db(repository: TenderRepository, tender_id: str) -> list[ExtractedDocument]:
    """Gespeicherte Extrakte zurueck in das Analysemodell wandeln."""
    documents: list[ExtractedDocument] = []
    for record in repository.extracts_for(tender_id):
        document = ExtractedDocument(
            source_path=str(record.document_id),
            file_name=(record.document.name if record.document else None)
            or (record.document.local_path if record.document else None),
            media_type=record.document.media_type if record.document else None,
            extractor=record.extractor,
            status=record.status,  # type: ignore[arg-type]
            error=record.error,
            metadata=dict(record.doc_metadata or {}),
            size_bytes=record.size_bytes,
            checksum_sha256=record.checksum_sha256,
            truncated=record.truncated,
            ocr_used=record.ocr_used,
        )
        # Der Volltext wird als eine Seite gefuehrt, wenn die Seitengrenzen
        # nicht mitgespeichert wurden; die Seitenzahl bleibt als Kennzahl.
        if record.text:
            document.pages.append(ExtractedPage(number=1, text=record.text))
        document.tables = [ExtractedTable.model_validate(table) for table in (record.tables or [])]
        documents.append(document)
    return documents
