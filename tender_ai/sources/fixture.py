"""Offline-Quelle aus einer lokalen JSON-Datei.

Zweck: die gesamte Pipeline laesst sich ohne Netzwerk demonstrieren und
testen. Die Datei enthaelt Beispiel-Ausschreibungen im Standardformat.
Fuer Produktivlaeufe ist diese Quelle in config.yaml deaktiviert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.errors import SourceError
from ..models.tender import Tender, make_tender_id
from .base import SearchQuery, TenderSource
from .registry import register_source


@register_source
class FixtureSource(TenderSource):
    type_name = "fixture"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.path = Path(str(getattr(self.config, "path", "data/fixtures/sample_tenders.json")))

    async def search(self, query: SearchQuery) -> list[Tender]:
        if not self.path.is_file():
            raise SourceError(self.name, f"Fixture-Datei nicht gefunden: {self.path}")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError(self.name, f"Fixture ist kein gueltiges JSON: {exc}") from exc

        records = payload.get("tenders") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise SourceError(self.name, "Fixture muss eine Liste von Ausschreibungen enthalten")

        results: list[Tender] = []
        for record in records:
            record = dict(record)
            record.setdefault("source", self.name)
            source_id = str(record.get("source_id") or record.get("id") or len(results))
            record["source_id"] = source_id
            record["id"] = make_tender_id(self.name, source_id)
            tender = Tender.model_validate(record)
            if query.matches(tender):
                results.append(tender)
            if len(results) >= query.max_results:
                break
        return results

    async def get_tender_details(self, tender_id: str) -> Tender | None:
        for tender in await self.search(SearchQuery(max_results=10_000)):
            if tender.id == tender_id or tender.source_id == tender_id:
                return tender
        return None
