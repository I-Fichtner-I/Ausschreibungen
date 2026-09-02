"""Stufe 3: Artikel einer Ausschreibung erkennen und speichern.

Setzt auf den in Stufe 2 ausgelesenen Unterlagen auf. Fehlen sie, werden sie
- wie bei der Analyse - vorher beschafft, damit ein Befehl vom Suchtreffer bis
zur Positionsliste genuegt.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..core.errors import ConfigError
from ..core.logging import get_logger
from ..database.repository import TenderRepository
from ..database.session import session_scope
from ..items import extract_items
from ..models.item import ItemExtractionResult
from .documents import documents_from_db, fetch_documents

log = get_logger(__name__)


async def extract_tender_items(
    settings: Settings,
    tender_id: str,
    *,
    fetch_missing: bool = True,
) -> ItemExtractionResult:
    """Positionen einer Ausschreibung erkennen und persistieren."""
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

        documents = documents_from_db(repository, resolved_id)
        result = extract_items(documents, tender_id=resolved_id)
        repository.save_items(result, record)
        session.commit()

        log.info(
            "items_extracted",
            tender=resolved_id,
            items=result.item_count,
            priceable=result.priceable_count,
            confidence=result.average_confidence,
            tables_used=result.tables_used,
        )
        return result


@dataclass(slots=True)
class BatchItemReport:
    """Ergebnis eines Stapellaufs der Artikelerkennung."""

    extracted: list[ItemExtractionResult] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.extracted)

    @property
    def item_count(self) -> int:
        return sum(result.item_count for result in self.extracted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenders": self.count,
            "items": self.item_count,
            "failed": self.failed,
            "results": [
                {
                    "tender_id": result.tender_id,
                    "items": result.item_count,
                    "priceable": result.priceable_count,
                    "confidence": result.average_confidence,
                }
                for result in sorted(self.extracted, key=lambda item: item.item_count, reverse=True)
            ],
        }


async def extract_items_for_open_tenders(
    settings: Settings,
    *,
    limit: int = 50,
    fetch_missing: bool = True,
    skip_extracted: bool = True,
) -> BatchItemReport:
    """Positionen aller laufenden Ausschreibungen erkennen.

    ``skip_extracted`` ueberspringt Ausschreibungen, deren Inhalt sich seit dem
    letzten Lauf nicht geaendert hat - verglichen wird der mitgespeicherte
    ``content_hash``, nicht ``updated_at``: der Lauf schreibt selbst in die
    Datenbank und wuerde sich sonst bei jedem Aufruf erneut ausloesen.
    Ein Fehler bei einer Ausschreibung stoppt den Stapel nicht.
    """
    with session_scope(settings.database_url) as session:
        repository = TenderRepository(session, settings.dedup)
        candidates: list[str] = []
        for record in repository.list_tenders(limit=limit, open_only=True, order_by="deadline"):
            already_current = (
                skip_extracted
                and record.item_extraction is not None
                and record.item_extraction.content_hash == record.content_hash
            )
            if already_current:
                continue
            candidates.append(record.id)

    report = BatchItemReport()
    for tender_id in candidates:
        try:
            report.extracted.append(
                await extract_tender_items(settings, tender_id, fetch_missing=fetch_missing)
            )
        except Exception as exc:  # noqa: BLE001 - eine Ausschreibung stoppt nie den Stapel
            log.error("item_extraction_failed", tender=tender_id, error=str(exc))
            report.failed.append({"tender_id": tender_id, "error": str(exc)})
    log.info("batch_items_done", tenders=report.count, items=report.item_count)
    return report


def extract_tender_items_sync(
    settings: Settings, tender_id: str, **kwargs: Any
) -> ItemExtractionResult:
    """Synchroner Einstieg fuer CLI und Skripte."""
    return asyncio.run(extract_tender_items(settings, tender_id, **kwargs))
