"""Einheitliches Interface fuer Ausschreibungsquellen.

Jede Quelle (API, RSS, HTML, lokale Datei) implementiert dieselbe Schnittstelle.
Neue Portale werden dadurch ergaenzt, ohne dass die Pipeline angefasst werden
muss: Adapterklasse schreiben, registrieren, in config.yaml aktivieren.
"""

from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from ..config import SearchConfig, Settings, SourceConfig
from ..core.http import HttpClient
from ..core.logging import get_logger
from ..models.common import normalize_text
from ..models.tender import Tender, TenderDocument

log = get_logger(__name__)

_SAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_PATH_COMPONENT = 120


def safe_document_path(base: Path, source: str, source_id: str, suffix: str) -> Path:
    """Sicheren Ablagepfad fuer ein Dokument bilden.

    ``source`` und ``source_id`` stammen aus Fremddaten und duerfen weder
    Verzeichnisse wechseln (``../``) noch unzulaessige Zeichen enthalten. Das
    Ergebnis liegt garantiert unterhalb von ``base``; sonst ``ValueError``.
    """

    def clean(component: str) -> str:
        cleaned = _SAFE_PATH_CHARS.sub("_", component).strip("._")
        if len(cleaned) > _MAX_PATH_COMPONENT or cleaned != component.strip("._"):
            # Verkuerzt oder veraendert: Hash anhaengen, damit unterschiedliche
            # Originalwerte nicht auf denselben Dateinamen fallen.
            digest = hashlib.sha256(component.encode()).hexdigest()[:12]
            cleaned = f"{cleaned[: _MAX_PATH_COMPONENT - 13]}_{digest}".strip("_")
        return cleaned or hashlib.sha256(component.encode()).hexdigest()[:24]

    safe_suffix = "".join(ch for ch in suffix if ch.isalnum() or ch == ".")[:16]
    base_resolved = base.resolve()
    target = (base_resolved / clean(source) / f"{clean(source_id)}{safe_suffix}").resolve()
    if not target.is_relative_to(base_resolved):
        raise ValueError(f"Dokumentpfad verlaesst das Basisverzeichnis: {target}")
    return target


@dataclass(slots=True)
class SearchQuery:
    """Suchanfrage in quellneutraler Form."""

    keywords: list[str] = field(default_factory=list)
    cpv_codes: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    published_after: date | None = None
    published_before: date | None = None
    deadline_after: datetime | None = None
    max_results: int = 100

    @classmethod
    def from_config(cls, config: SearchConfig) -> SearchQuery:
        now = datetime.now(UTC)
        published_after = None
        if config.published_within_days:
            published_after = now.date() - timedelta(days=config.published_within_days)
        deadline_after = None
        if config.min_days_until_deadline:
            deadline_after = now + timedelta(days=config.min_days_until_deadline)
        return cls(
            keywords=list(config.keywords),
            cpv_codes=list(config.cpv_codes),
            countries=list(config.countries),
            published_after=published_after,
            deadline_after=deadline_after,
            max_results=config.max_results_per_source,
        )

    def matches(self, tender: Tender) -> bool:
        """Clientseitige Filterung fuer Quellen ohne serverseitige Filter (z. B. RSS).

        Bewusst tolerant: fehlende Angaben fuehren nicht zum Ausschluss, weil
        Bekanntmachungen oft unvollstaendig sind. Ausgeschlossen wird nur, was
        einem gesetzten Kriterium nachweislich widerspricht.
        """
        if self.keywords:
            haystack = normalize_text(
                " ".join(
                    filter(
                        None,
                        [tender.title, tender.description, tender.contracting_authority],
                    )
                )
            )
            if not any(normalize_text(kw) in haystack for kw in self.keywords):
                return False
        if self.cpv_codes and tender.cpv_codes:
            wanted = {code[:8] for code in self.cpv_codes}
            found = {code[:8] for code in tender.cpv_codes}
            # Praefix-Vergleich: 30231300 passt auch auf 30230000-Suchen
            if not any(
                any(f.startswith(w.rstrip("0")) or w.startswith(f.rstrip("0")) for w in wanted)
                for f in found
            ):
                return False
        if (
            self.countries
            and tender.country
            and tender.country.upper() not in {c.upper() for c in self.countries}
        ):
            return False
        if (
            self.published_after
            and tender.publication_date
            and tender.publication_date < self.published_after
        ):
            return False
        if (
            self.published_before
            and tender.publication_date
            and tender.publication_date > self.published_before
        ):
            return False
        return not (
            self.deadline_after
            and tender.submission_deadline
            and tender.submission_deadline < self.deadline_after
        )


@dataclass(slots=True)
class SourceStatus:
    """Ergebnis eines Health-Checks - Basis fuer ``tender-ai doctor``."""

    name: str
    type: str
    ok: bool
    message: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sample_count: int = 0
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "ok": self.ok,
            "message": self.message,
            "checked_at": self.checked_at.isoformat(),
            "sample_count": self.sample_count,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class TenderSource(ABC):
    """Basisklasse aller Quellen."""

    type_name: ClassVar[str] = "base"
    #: Erlaubt der Quelle, die robots.txt-Pruefung zu ueberspringen. Nur fuer
    #: offizielle APIs mit eigenen Nutzungsbedingungen zulaessig - niemals, um
    #: eine Zugriffsbeschraenkung zu umgehen.
    is_official_api: ClassVar[bool] = False

    def __init__(
        self,
        name: str,
        config: SourceConfig,
        http: HttpClient,
        settings: Settings,
    ) -> None:
        self.name = name
        self.config = config
        self.http = http
        self.settings = settings
        self.log = get_logger(f"source.{name}")

    @property
    def priority(self) -> int:
        return self.config.priority

    def _register_rate_limit(self, *urls: str) -> None:
        """Quellspezifisches Rate-Limit fuer die betroffenen Hosts setzen."""
        for url in urls:
            if url:
                self.http.configure_host_rate(url, self.config.requests_per_second)

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[Tender]:
        """Ausschreibungen suchen und als ``Tender`` zurueckgeben."""

    async def get_tender_details(self, tender_id: str) -> Tender | None:
        """Details zu einer Ausschreibung nachladen.

        Default: nicht unterstuetzt. Quellen mit Detailendpunkt ueberschreiben
        die Methode.
        """
        return None

    async def download_documents(self, tender: Tender, destination: Path) -> list[TenderDocument]:
        """Frei zugaengliche Unterlagen herunterladen.

        Geschuetzte Dokumente (Login, Captcha, Paywall) werden bewusst nicht
        abgerufen, sondern mit entsprechendem ``access``-Status vermerkt.
        Zielpfade sind ausschliesslich ueber ``safe_document_path`` zu bilden.
        """
        return []

    async def health_check(self) -> SourceStatus:
        """Erreichbarkeit und Parsing mit einer minimalen Suche pruefen."""
        started = time.perf_counter()
        try:
            results = await self.search(SearchQuery(max_results=1))
        except Exception as exc:  # noqa: BLE001 - doctor soll jeden Fehler melden, nie abstuerzen
            return SourceStatus(
                name=self.name,
                type=self.type_name,
                ok=False,
                message=f"{type(exc).__name__}: {exc}",
                duration_seconds=time.perf_counter() - started,
            )
        return SourceStatus(
            name=self.name,
            type=self.type_name,
            ok=True,
            message=f"{len(results)} Treffer im Probeabruf",
            sample_count=len(results),
            duration_seconds=time.perf_counter() - started,
        )
