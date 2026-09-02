"""Dublettenerkennung ueber Quellen hinweg.

Dieselbe Ausschreibung erscheint oft auf mehreren Portalen. Erkannt wird in
drei Stufen, von hart nach weich:

1. amtliche Vergabe-/Bekanntmachungsnummer identisch  -> Konfidenz 100
2. Fingerprint identisch (Titel + Vergabestelle + Frist) -> Konfidenz 98
3. Titel- und Vergabestellen-Aehnlichkeit innerhalb der Blocking-Gruppe
   (indizierter Schluessel aus Vergabestelle + Titelanfang) -> berechnete Konfidenz

Unterhalb der Schwelle wird bewusst **keine** Zusammenfuehrung vorgenommen -
zwei getrennte Datensaetze sind harmloser als eine falsche Verschmelzung.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..config import DedupConfig
from ..database.models import TenderRecord
from ..models.common import blocking_key, normalize_text
from ..models.tender import Tender


@dataclass(slots=True)
class DuplicateMatch:
    record: TenderRecord
    reason: str
    confidence: int


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


class DuplicateDetector:
    def __init__(self, config: DedupConfig) -> None:
        self.config = config

    def find(self, session: Session, tender: Tender) -> DuplicateMatch | None:
        if not self.config.enabled:
            return None

        # 1) amtliche Nummer
        if tender.national_id:
            stmt = select(TenderRecord).where(
                TenderRecord.national_id == tender.national_id,
                TenderRecord.source != tender.source,
            )
            record = session.scalars(stmt).first()
            if record is not None:
                return DuplicateMatch(record, "national_id", 100)

        # 2) Fingerprint
        stmt = select(TenderRecord).where(
            TenderRecord.fingerprint == tender.fingerprint(),
            TenderRecord.source != tender.source,
        )
        record = session.scalars(stmt).first()
        if record is not None:
            return DuplicateMatch(record, "fingerprint", 98)

        # 3) Titel-/Vergabestellen-Aehnlichkeit innerhalb der Blocking-Gruppe
        title = normalize_text(tender.title)
        if not title:
            return None

        best: DuplicateMatch | None = None
        authority = normalize_text(tender.contracting_authority)
        for candidate in self._candidates(session, tender):
            match = self._score(title, authority, candidate)
            if match is not None and (best is None or match.confidence > best.confidence):
                best = match
        return best

    def _window_filter(self, stmt: Select[Any], tender: Tender) -> Select[Any]:
        if tender.publication_date is None:
            return stmt
        window = timedelta(days=self.config.match_window_days)
        return stmt.where(
            TenderRecord.publication_date.is_(None)
            | (
                TenderRecord.publication_date.between(
                    tender.publication_date - window,
                    tender.publication_date + window,
                )
            )
        )

    def _candidates(self, session: Session, tender: Tender) -> Iterator[TenderRecord]:
        """Kandidaten ueber den indizierten Blocking-Schluessel einschraenken.

        Frueher wurden bis zu 500 beliebige Datensaetze im Zeitfenster geladen und
        alle in Python verglichen - das war bei gefuellter Datenbank langsam und
        hat Dubletten jenseits des Limits gar nicht mehr gefunden. Jetzt gilt:

        1. exakt gleiche Blocking-Gruppe (Vergabestelle + Titelanfang) - ohne Limit
        2. gleiche Vergabestelle, abweichender Titelanfang - begrenzt, damit eine
           Behoerde mit sehr vielen Ausschreibungen den Lauf nicht ausbremst
        """
        seen: set[str] = set()
        key = blocking_key(tender.title, tender.contracting_authority)
        stmt = self._window_filter(
            select(TenderRecord).where(
                TenderRecord.blocking_key == key,
                TenderRecord.source != tender.source,
            ),
            tender,
        )
        for candidate in session.scalars(stmt):
            seen.add(candidate.id)
            yield candidate

        authority = normalize_text(tender.contracting_authority)
        if not authority:
            return
        stmt = (
            self._window_filter(
                select(TenderRecord).where(
                    TenderRecord.authority_normalized == authority,
                    TenderRecord.blocking_key != key,
                    TenderRecord.source != tender.source,
                ),
                tender,
            )
            .order_by(TenderRecord.last_seen_at.desc())
            .limit(self.config.max_authority_candidates)
        )
        for candidate in session.scalars(stmt):
            if candidate.id not in seen:
                yield candidate

    def _score(self, title: str, authority: str, candidate: TenderRecord) -> DuplicateMatch | None:
        title_score = similarity(title, candidate.title_normalized or "")
        if title_score < self.config.title_similarity_threshold:
            return None
        authority_score = (
            similarity(authority, candidate.authority_normalized or "")
            if authority and candidate.authority_normalized
            else None
        )
        # Ohne vergleichbare Vergabestelle ist ein sehr hoher Titeltreffer noetig.
        if authority_score is None:
            if title_score < 0.97:
                return None
            confidence = int(title_score * 90)
        else:
            if authority_score < 0.80:
                return None
            confidence = int((title_score * 0.7 + authority_score * 0.3) * 100)
        return DuplicateMatch(candidate, "title_similarity", confidence)
