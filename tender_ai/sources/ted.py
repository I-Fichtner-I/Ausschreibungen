"""TED (Tenders Electronic Daily) - offizielle Such-API des EU-Amtsblatts S.

TED ist die erste Wahl fuer EU-weite Bekanntmachungen oberhalb der
Schwellenwerte: offene, ausdruecklich fuer den maschinellen Zugriff
bereitgestellte API, keine Zugriffsbeschraenkung, die umgangen werden muesste.

Wichtig: Endpunkt, Feldliste, die Feldnamen der Expert-Query und das
Status-Mapping stehen in config.yaml, weil TED seine API und die
eForms-Codes versioniert. Bei einer Aenderung wird die Konfiguration
angepasst - nicht dieser Code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import TedSourceConfig
from ..core.errors import SourceError
from ..models.common import Provenance, utcnow
from ..models.tender import (
    DocumentAccess,
    Tender,
    TenderDocument,
    TenderStatus,
    make_tender_id,
)
from .base import SearchQuery, TenderSource, safe_document_path
from .parsing import (
    all_texts,
    first_text,
    parse_amount,
    parse_currency,
    parse_date,
    parse_datetime,
    strip_html,
)
from .registry import register_source

DEFAULT_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "publication-date",
    "deadline-receipt-request",
    "classification-cpv",
    "notice-type",
    "procedure-type",
    "total-value",
    "links",
]

DEFAULT_QUERY_FIELDS = {
    "fulltext": "FT",
    "cpv": "classification-cpv",
    "country": "buyer-country",
    "publication_date": "publication-date",
    "deadline": "deadline-receipt-request",
}

#: Praefix des eForms-``notice-type`` -> Status. Reihenfolge = Prioritaet.
#: Ueberschreibbar ueber ``sources.ted.status_map`` in config.yaml.
DEFAULT_STATUS_MAP: dict[str, str] = {
    "cn-": "OPEN",  # contract notice
    "pin-": "OPEN",  # prior information notice (als Aufruf zum Wettbewerb)
    "qs-": "OPEN",  # qualification system
    "subco": "OPEN",  # subcontracting notice
    "can-": "AWARDED",  # contract award notice
    "corr": "AMENDED",  # corrigendum
    "change": "AMENDED",  # change notice
}

MAX_PAGES = 20


@register_source
class TedSource(TenderSource):
    type_name = "ted"
    is_official_api = True

    config: TedSourceConfig

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = self.config.base_url.rstrip("/")
        self.search_path = self.config.search_path
        self.page_size = self.config.page_size or 50
        self.fields = list(self.config.fields or DEFAULT_FIELDS)
        self.query_fields = {**DEFAULT_QUERY_FIELDS, **(self.config.query_fields or {})}
        self.status_map: dict[str, str] = {
            str(k).lower(): str(v).upper()
            for k, v in (self.config.status_map or DEFAULT_STATUS_MAP).items()
        }
        self.raw_query = self.config.raw_query
        self.api_key = self.settings.secret_for_source(self.name)
        self._register_rate_limit(self.base_url)

    # --- Query-Bau ---------------------------------------------------------
    def build_expert_query(self, query: SearchQuery) -> str:
        if self.raw_query:
            return str(self.raw_query)

        fragments: list[str] = []
        ft_field = self.query_fields.get("fulltext")
        if query.keywords and ft_field:
            terms = " OR ".join(
                f'{ft_field} ~ ("{kw.strip()}")' for kw in query.keywords if kw.strip()
            )
            if terms:
                fragments.append(f"({terms})")

        cpv_field = self.query_fields.get("cpv")
        if query.cpv_codes and cpv_field:
            codes = " ".join(code.strip() for code in query.cpv_codes if code.strip())
            if codes:
                fragments.append(f"{cpv_field} IN ({codes})")

        country_field = self.query_fields.get("country")
        if query.countries and country_field:
            countries = " ".join(c.strip().upper() for c in query.countries if c.strip())
            if countries:
                fragments.append(f"{country_field} IN ({countries})")

        pub_field = self.query_fields.get("publication_date")
        if pub_field:
            published_after = query.published_after or (
                datetime.now(UTC).date() - timedelta(days=7)
            )
            fragments.append(f"{pub_field} >= {published_after.strftime('%Y%m%d')}")
            if query.published_before:
                fragments.append(f"{pub_field} <= {query.published_before.strftime('%Y%m%d')}")

        deadline_field = self.query_fields.get("deadline")
        if query.deadline_after and deadline_field:
            fragments.append(f"{deadline_field} >= {query.deadline_after.strftime('%Y%m%d')}")

        return " AND ".join(fragments)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key is not None:
            scheme = self.config.auth_scheme.strip()
            headers[self.config.auth_header] = f"{scheme} {self.api_key.get_secret_value()}".strip()
        return headers

    # --- Suche -------------------------------------------------------------
    async def search(self, query: SearchQuery) -> list[Tender]:
        expert_query = self.build_expert_query(query)
        url = f"{self.base_url}{self.search_path}"
        collected: list[Tender] = []
        seen_ids: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            remaining = query.max_results - len(collected)
            if remaining <= 0:
                break
            limit = min(self.page_size, max(remaining, 1))
            body: dict[str, Any] = {
                "query": expert_query,
                "fields": self.fields,
                "page": page,
                "limit": limit,
                "scope": self.config.scope,
            }
            response = await self.http.post(
                url,
                json=body,
                headers=self._headers(),
                # Offizielle API mit eigenen Nutzungsbedingungen: die robots.txt
                # des Webportals gilt hier nicht. Rate-Limit bleibt aktiv.
                check_robots=False,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceError(self.name, f"Antwort ist kein JSON: {exc}") from exc

            # Bewusst streng: fehlt der Ergebnisschluessel, hat sich die API
            # geaendert. Das muss auffallen und darf nicht als "0 Treffer"
            # durchgehen.
            notices: Any = None
            for key in ("notices", "results", "data"):
                if key in payload:
                    notices = payload[key]
                    break
            if not isinstance(notices, list):
                raise SourceError(
                    self.name,
                    "Unerwartete Antwortstruktur - kein Ergebnisfeld "
                    f"(notices/results/data) gefunden. Vorhandene Schluessel: "
                    f"{sorted(payload)[:10]}. Bitte Endpunkt/Feldliste in "
                    "config.yaml pruefen.",
                )
            if not notices:
                break

            for notice in notices:
                tender = self._to_tender(notice)
                if tender is None or tender.id in seen_ids:
                    continue
                seen_ids.add(tender.id)
                collected.append(tender)
                if len(collected) >= query.max_results:
                    break

            if len(notices) < limit:
                break

        self.log.info("ted_search_done", results=len(collected), query=expert_query[:200])
        return collected

    async def get_tender_details(self, tender_id: str) -> Tender | None:
        """Einzelne Bekanntmachung ueber ihre Veroeffentlichungsnummer laden."""
        publication_number = tender_id.split(":", 1)[-1]
        body: dict[str, Any] = {
            "query": f"publication-number={publication_number}",
            "fields": self.fields,
            "page": 1,
            "limit": 1,
            "scope": "ALL",
        }
        response = await self.http.post(
            f"{self.base_url}{self.search_path}",
            json=body,
            headers=self._headers(),
            check_robots=False,
        )
        payload = response.json()
        notices = payload.get("notices") or payload.get("results") or []
        if not notices:
            return None
        return self._to_tender(notices[0])

    async def download_documents(self, tender: Tender, destination: Path) -> list[TenderDocument]:
        """Frei zugaengliche TED-Dokumente (PDF/XML) herunterladen."""
        downloaded: list[TenderDocument] = []
        for document in tender.documents:
            if document.access is not DocumentAccess.PUBLIC or not document.url:
                continue
            suffix = ".pdf" if (document.media_type or "").endswith("pdf") else ".xml"
            target = safe_document_path(destination, tender.source, tender.source_id, suffix)
            try:
                path = await self.http.download(document.url, target, check_robots=False)

            except Exception as exc:  # noqa: BLE001 - ein fehlgeschlagener Download stoppt nicht die uebrigen
                self.log.warning("document_download_failed", url=document.url, error=str(exc))
                document.note = f"Download fehlgeschlagen: {exc}"
                continue
            document.local_path = str(path)
            document.retrieved_at = utcnow()
            downloaded.append(document)
        return downloaded

    # --- Mapping -----------------------------------------------------------
    def _status_from(self, notice_type: str | None, deadline: datetime | None) -> TenderStatus:
        """Status aus eForms-``notice-type`` ableiten; die Frist ist nur Zusatzkriterium."""
        now = utcnow()
        mapped: TenderStatus | None = None
        if notice_type:
            lowered = notice_type.lower()
            for prefix, status_name in self.status_map.items():
                if lowered.startswith(prefix):
                    mapped = TenderStatus(status_name)
                    break
        if mapped is None:
            # Typ unbekannt: nur eine vorhandene Frist erlaubt eine Aussage.
            if deadline is None:
                return TenderStatus.UNKNOWN
            return TenderStatus.OPEN if deadline > now else TenderStatus.CLOSED
        if mapped is TenderStatus.OPEN and deadline is not None and deadline <= now:
            return TenderStatus.CLOSED
        return mapped

    def _to_tender(self, notice: dict[str, Any]) -> Tender | None:
        publication_number = first_text(
            notice.get("publication-number") or notice.get("publicationNumber")
        )
        if not publication_number:
            self.log.warning("ted_notice_without_id", keys=sorted(notice)[:10])
            return None

        links = notice.get("links") or {}
        html_link = self._link(links, "html") or self._link(links, "htmlDirect")
        source_url = html_link or (f"https://ted.europa.eu/en/notice/-/detail/{publication_number}")

        deadline = parse_datetime(
            notice.get("deadline-receipt-request") or notice.get("deadline-receipt-tender")
        )
        value_raw = notice.get("total-value") or notice.get("estimated-value-lot")
        notice_type = first_text(notice.get("notice-type"))

        return Tender(
            id=make_tender_id(self.name, publication_number),
            source=self.name,
            source_id=publication_number,
            source_url=source_url,
            national_id=publication_number,
            title=first_text(notice.get("notice-title")),
            contracting_authority=first_text(notice.get("buyer-name")),
            description=strip_html(first_text(notice.get("description-lot"))),
            country=first_text(notice.get("buyer-country")),
            region=first_text(notice.get("place-performance-country-part")),
            cpv_codes=all_texts(notice.get("classification-cpv")),
            notice_type=notice_type,
            procedure_type=first_text(notice.get("procedure-type")),
            publication_date=parse_date(notice.get("publication-date")),
            submission_deadline=deadline,
            estimated_value=parse_amount(value_raw),
            currency=parse_currency(value_raw),
            status=self._status_from(notice_type, deadline),
            documents=self._documents(links),
            provenance=Provenance(
                source=self.name,
                source_id=publication_number,
                source_url=source_url,
                method="api",
            ),
            raw=notice,
        )

    @staticmethod
    def _link(links: Any, kind: str) -> str | None:
        """URL aus der verschachtelten TED-``links``-Struktur ziehen."""
        if not isinstance(links, dict):
            return None
        return first_text(links.get(kind))

    def _documents(self, links: Any) -> list[TenderDocument]:
        documents: list[TenderDocument] = []
        for kind, media_type in (("pdf", "application/pdf"), ("xml", "application/xml")):
            url = self._link(links, kind)
            if url:
                documents.append(
                    TenderDocument(
                        name=f"TED-Bekanntmachung ({kind.upper()})",
                        url=url,
                        media_type=media_type,
                        access=DocumentAccess.PUBLIC,
                    )
                )
        return documents
