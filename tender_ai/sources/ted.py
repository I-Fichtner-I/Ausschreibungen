"""TED (Tenders Electronic Daily) - offizielle Such-API des EU-Amtsblatts S.

TED ist die erste Wahl fuer EU-weite Bekanntmachungen oberhalb der
Schwellenwerte: offene, ausdruecklich fuer den maschinellen Zugriff
bereitgestellte API, keine Zugriffsbeschraenkung, die umgangen werden muesste.

Wichtig: Endpunkt, Feldliste und die Feldnamen der Expert-Query stehen in
config.yaml, weil TED seine API versioniert. Bei einer API-Aenderung wird die
Konfiguration angepasst - nicht dieser Code.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..core.errors import SourceError
from ..models.tender import (
    DocumentAccess,
    Tender,
    TenderDocument,
    TenderStatus,
    make_tender_id,
)
from ..models.common import Provenance, utcnow
from .base import SearchQuery, TenderSource
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

MAX_PAGES = 20


@register_source
class TedSource(TenderSource):
    type_name = "ted"
    is_official_api = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = str(getattr(self.config, "base_url", "https://api.ted.europa.eu")).rstrip("/")
        self.search_path = str(getattr(self.config, "search_path", "/v3/notices/search"))
        self.page_size = int(getattr(self.config, "page_size", 50) or 50)
        self.fields = list(getattr(self.config, "fields", None) or DEFAULT_FIELDS)
        self.query_fields = {
            **DEFAULT_QUERY_FIELDS,
            **(getattr(self.config, "query_fields", None) or {}),
        }
        self.raw_query = getattr(self.config, "raw_query", None)
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
            published_after = query.published_after or (date.today() - timedelta(days=7))
            fragments.append(f"{pub_field} >= {published_after.strftime('%Y%m%d')}")
            if query.published_before:
                fragments.append(
                    f"{pub_field} <= {query.published_before.strftime('%Y%m%d')}"
                )

        deadline_field = self.query_fields.get("deadline")
        if query.deadline_after and deadline_field:
            fragments.append(
                f"{deadline_field} >= {query.deadline_after.strftime('%Y%m%d')}"
            )

        return " AND ".join(fragments)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            header_name = str(getattr(self.config, "auth_header", "Authorization"))
            scheme = str(getattr(self.config, "auth_scheme", "") or "").strip()
            headers[header_name] = f"{scheme} {self.api_key}".strip()
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
            body = {
                "query": expert_query,
                "fields": self.fields,
                "page": page,
                "limit": min(self.page_size, max(remaining, 1)),
                "scope": str(getattr(self.config, "scope", "ALL")),
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

            if len(notices) < body["limit"]:
                break

        self.log.info(
            "ted_search_done", results=len(collected), query=expert_query[:200]
        )
        return collected

    async def get_tender_details(self, tender_id: str) -> Tender | None:
        """Einzelne Bekanntmachung ueber ihre Veroeffentlichungsnummer laden."""
        publication_number = tender_id.split(":", 1)[-1]
        pub_field = "publication-number"
        body = {
            "query": f"{pub_field}={publication_number}",
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

    async def download_documents(
        self, tender: Tender, destination: Path
    ) -> list[TenderDocument]:
        """Frei zugaengliche TED-Dokumente (PDF/XML) herunterladen."""
        downloaded: list[TenderDocument] = []
        for document in tender.documents:
            if document.access is not DocumentAccess.PUBLIC or not document.url:
                continue
            suffix = ".pdf" if (document.media_type or "").endswith("pdf") else ".xml"
            target = destination / tender.source / f"{tender.source_id}{suffix}"
            try:
                path = await self.http.download(document.url, target, check_robots=False)
            except Exception as exc:
                self.log.warning(
                    "document_download_failed", url=document.url, error=str(exc)
                )
                document.note = f"Download fehlgeschlagen: {exc}"
                continue
            document.local_path = str(path)
            document.retrieved_at = utcnow()
            downloaded.append(document)
        return downloaded

    # --- Mapping -----------------------------------------------------------
    def _to_tender(self, notice: dict[str, Any]) -> Tender | None:
        publication_number = first_text(
            notice.get("publication-number") or notice.get("publicationNumber")
        )
        if not publication_number:
            self.log.warning("ted_notice_without_id", keys=sorted(notice)[:10])
            return None

        links = notice.get("links") or {}
        html_link = self._link(links, "html") or self._link(links, "htmlDirect")
        source_url = html_link or (
            f"https://ted.europa.eu/en/notice/-/detail/{publication_number}"
        )

        deadline = parse_datetime(
            notice.get("deadline-receipt-request")
            or notice.get("deadline-receipt-tender")
        )
        value_raw = notice.get("total-value") or notice.get("estimated-value-lot")

        tender = Tender(
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
            notice_type=first_text(notice.get("notice-type")),
            procedure_type=first_text(notice.get("procedure-type")),
            publication_date=parse_date(notice.get("publication-date")),
            submission_deadline=deadline,
            estimated_value=parse_amount(value_raw),
            currency=parse_currency(value_raw),
            status=TenderStatus.OPEN if deadline is None or deadline > utcnow() else TenderStatus.CLOSED,
            documents=self._documents(links),
            provenance=Provenance(
                source=self.name,
                source_id=publication_number,
                source_url=source_url,
                method="api",
            ),
            raw=notice,
        )
        return tender

    @staticmethod
    def _link(links: Any, kind: str) -> str | None:
        """URL aus der verschachtelten TED-``links``-Struktur ziehen."""
        if not isinstance(links, dict):
            return None
        entry = links.get(kind)
        return first_text(entry)

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
