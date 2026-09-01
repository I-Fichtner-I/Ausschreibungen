"""Dublettenerkennung ueber Quellen hinweg.

Dieselbe Ausschreibung erscheint oft auf mehreren Portalen. Erkannt wird in
drei Stufen, von hart nach weich:

1. amtliche Vergabe-/Bekanntmachungsnummer identisch  -> Konfidenz 100
2. Fingerprint identisch (Titel + Vergabestelle + Frist) -> Konfidenz 98
3. Titel- und Vergabestellen-Aehnlichkeit im Zeitfenster -> berechnete Konfidenz

Unterhalb der Schwelle wird bewusst **keine** Zusammenfuehrung vorgenommen -
zwei getrennte Datensaetze sind harmloser als eine falsche Verschmelzung.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DedupConfig
from ..database.models import TenderRecord
from ..models.common import normalize_text
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

        # 3) Titel-/Vergabestellen-Aehnlichkeit im Zeitfenster
        title = normalize_text(tender.title)
        if not title:
            return None
        stmt = select(TenderRecord).where(TenderRecord.source != tender.source)
        if tender.publication_date is not None:
            window = timedelta(days=self.config.match_window_days)
            stmt = stmt.where(
                TenderRecord.publication_date.is_(None)
                | (
                    TenderRecord.publication_date.between(
                        tender.publication_date - window,
                        tender.publication_date + window,
                    )
                )
            )
        stmt = stmt.order_by(TenderRecord.last_seen_at.desc()).limit(500)

        authority = normalize_text(tender.contracting_authority)
        best: DuplicateMatch | None = None
        for candidate in session.scalars(stmt):
            title_score = similarity(title, candidate.title_normalized or "")
            if title_score < self.config.title_similarity_threshold:
                continue
            authority_score = (
                similarity(authority, candidate.authority_normalized or "")
                if authority and candidate.authority_normalized
                else None
            )
            # Ohne vergleichbare Vergabestelle ist ein sehr hoher Titeltreffer noetig.
            if authority_score is None:
                if title_score < 0.97:
                    continue
                confidence = int(title_score * 90)
            else:
                if authority_score < 0.80:
                    continue
                confidence = int((title_score * 0.7 + authority_score * 0.3) * 100)
            if best is None or confidence > best.confidence:
                best = DuplicateMatch(candidate, "title_similarity", confidence)
        return best
