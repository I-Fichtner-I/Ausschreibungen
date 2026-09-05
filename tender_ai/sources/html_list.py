"""Vergabeportal ohne API: die oeffentliche Trefferliste auslesen.

Wenn ein Portal keine Schnittstelle anbietet, bleibt nur die HTML-Seite. Dieser
Adapter macht daraus einen konfigurierbaren Vorgang statt einer Sammlung
handgeschriebener Parser: Zeilenselektor plus Feldselektoren stehen in
config.yaml, ein geaendertes Markup ist damit eine Konfigurationsaenderung.

**Was der Adapter nicht tut.** Er ruft ausschliesslich frei erreichbare
Uebersichts- und Detailseiten ab, ueber den normalen HTTP-Client und damit mit
robots.txt-Pruefung, Rate-Limit und Cache. Er meldet sich nirgends an, loest
keine Captchas und umgeht keine Zugriffsbeschraenkung. Ist eine Seite gesperrt,
wird sie uebersprungen und der Grund protokolliert.

**Selektoren pruefen statt raten.** ``tender-ai doctor --source <name>`` ruft
die Liste einmal ab und meldet je Feld, in wie vielen Zeilen es gefunden wurde.
Ein Feld mit "0/20" zeigt sofort auf den falschen Selektor - ohne dass ein
falsch geparster Wert je in die Datenbank gelangt.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup, Tag

from ..config import FieldSelector, HtmlListSourceConfig
from ..core.errors import RobotsDisallowedError, SourceError
from ..models.common import Provenance
from ..models.tender import Tender, TenderStatus, make_tender_id
from .base import SearchQuery, SourceStatus, TenderSource
from .parsing import parse_amount, parse_currency, parse_date
from .registry import register_source

#: Feldnamen, die der Adapter versteht. Alles andere in ``fields`` landet in
#: ``raw`` - so geht eine Portalbesonderheit nicht verloren, wird aber auch
#: nicht in ein Feld gepresst, in das sie nicht gehoert.
FIELDS = (
    "title",
    "detail_url",
    "contracting_authority",
    "description",
    "national_id",
    "notice_type",
    "procedure_type",
    "region",
    "cpv_codes",
    "publication_date",
    "submission_deadline",
    "estimated_value",
    "currency",
    "document_url",
)

#: Datum mit optionaler Uhrzeit: "12.09.2026", "12.09.2026, 10:00 Uhr".
_DATE_TIME = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"(?:[^\d]{1,8}(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
#: ISO-Schreibweise, wie sie in ``datetime``-Attributen vorkommt.
_ISO_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
#: CPV-Codes stehen als 8-stellige Zahl, teils mit Pruefziffer.
_CPV = re.compile(r"\b(\d{8})(?:-\d)?\b")
_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = _WS.sub(" ", text).strip()
    return collapsed or None


@register_source
class HtmlListSource(TenderSource):
    type_name = "html_list"

    config: HtmlListSourceConfig

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._register_rate_limit(self.config.list_url, self.config.base_url)
        self._timezone = self._resolve_timezone(self.config.timezone)

    def _resolve_timezone(self, name: str) -> Any:
        """Ortszeit der Fristangaben; ohne Zeitzonendaten bleibt es UTC."""
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            self.log.warning("timezone_unavailable", timezone=name, fallback="UTC")
            return UTC

    # -- Abruf ---------------------------------------------------------------

    def _page_url(self, page: int) -> str:
        """Listen-URL fuer eine Seite; ohne ``page_param`` immer dieselbe."""
        if not self.config.page_param or page == self.config.first_page:
            return self.config.list_url
        parts = urlsplit(self.config.list_url)
        query = f"{parts.query}&" if parts.query else ""
        query = f"{query}{self.config.page_param}={page}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    async def _soup(self, url: str) -> BeautifulSoup:
        response = await self.http.get(url)
        limit = self.http.config.max_feed_bytes
        if limit and len(response.content) > limit:
            raise SourceError(
                self.name,
                f"{url}: Seite ist groesser als max_feed_bytes ({limit} Bytes) "
                f"und wird nicht geparst",
            )
        return BeautifulSoup(response.text, "lxml")

    async def search(self, query: SearchQuery) -> list[Tender]:
        results: list[Tender] = []
        failures: list[str] = []
        pages = 0
        detail_requests = 0

        for offset in range(max(1, self.config.max_pages)):
            page = self.config.first_page + offset
            url = self._page_url(page)
            try:
                soup = await self._soup(url)
            except RobotsDisallowedError as exc:
                # Kein Umgehen: Seite wird uebersprungen und protokolliert.
                self.log.warning("page_disallowed_by_robots", url=url, error=str(exc))
                failures.append(f"{url}: durch robots.txt untersagt")
                break
            except Exception as exc:  # noqa: BLE001 - eine Seite stoppt nicht den Lauf
                self.log.error("page_fetch_failed", url=url, error=str(exc))
                failures.append(f"{url}: {exc}")
                continue

            pages += 1
            rows = self._rows(soup)
            if not rows:
                # Leere Folgeseite heisst: Ende der Liste, nicht Fehler.
                break

            for row in rows:
                tender = self._to_tender(row, url)
                if tender is None:
                    continue
                if (
                    self.config.follow_detail
                    and tender.source_url
                    and detail_requests < self.config.max_detail_requests
                ):
                    detail_requests += 1
                    await self._enrich_from_detail(tender)
                if query.matches(tender):
                    results.append(tender)
                if len(results) >= query.max_results:
                    return results

        # Kein einziger Seitenabruf erfolgreich: ein stilles "0 Treffer" waere
        # irrefuehrend, die Quelle meldet den Fehler.
        if failures and pages == 0:
            raise SourceError(self.name, "; ".join(failures))
        return results

    def _rows(self, soup: BeautifulSoup) -> list[Tag]:
        return [row for row in soup.select(self.config.row_selector) if isinstance(row, Tag)]

    # -- Felder --------------------------------------------------------------

    def _value(self, element: Tag, spec: FieldSelector) -> str | None:
        """Einen Feldwert aus einer Zeile lesen; nichts gefunden -> ``None``."""
        target: Tag | None = element
        if spec.selector:
            found = [node for node in element.select(spec.selector) if isinstance(node, Tag)]
            if len(found) <= spec.index:
                return None
            target = found[spec.index]
        if target is None:
            return None

        raw: str | None
        if spec.attribute == "text":
            raw = target.get_text(" ", strip=True)
        else:
            attribute = target.get(spec.attribute)
            if isinstance(attribute, list):  # z. B. class="a b"
                attribute = " ".join(attribute)
            raw = attribute if isinstance(attribute, str) else None

        value = _clean(raw)
        if value is None or not spec.regex:
            return value
        match = re.search(spec.regex, value)
        if match is None:
            return None
        return _clean(match.group(1) if match.groups() else match.group(0))

    def _read_fields(self, element: Tag, specs: dict[str, FieldSelector]) -> dict[str, str | None]:
        return {name: self._value(element, spec) for name, spec in specs.items()}

    def _parse_deadline(self, text: str | None) -> datetime | None:
        """Frist als Ortszeit lesen - Portale schreiben sie ohne Offset."""
        if not text:
            return None
        iso = _ISO_DATE_TIME.search(text)
        if iso:
            try:
                parsed = datetime.fromisoformat(iso.group(0))
            except ValueError:
                parsed = None
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=self._timezone)
        match = _DATE_TIME.search(text)
        if match is None:
            return None
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour") or 0),
                int(match.group("minute") or 0),
                tzinfo=self._timezone,
            )
        except ValueError:
            return None

    def _source_id(self, detail_url: str | None, values: dict[str, str | None]) -> str:
        """Stabile Quell-ID - nie zufaellig, sonst waere jeder Lauf ein Neufund.

        Bevorzugt der ausgewiesene Bezeichner des Portals, sonst der eindeutige
        Query-Parameter der Detail-URL, sonst die URL selbst; erst als letztes
        ein Hash aus Titel und Vergabestelle.
        """
        national_id = values.get("national_id")
        if national_id:
            return national_id[:120]
        if detail_url:
            if self.config.id_param:
                found = parse_qs(urlparse(detail_url).query).get(self.config.id_param)
                if found and found[0].strip():
                    return found[0].strip()[:120]
            return hashlib.sha256(detail_url.encode()).hexdigest()[:24]
        fingerprint = "|".join(
            filter(None, (values.get("title"), values.get("contracting_authority")))
        )
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:24]

    def _to_tender(self, row: Tag, page_url: str) -> Tender | None:
        values = self._read_fields(row, self.config.fields)
        missing = [name for name in self.config.required_fields if not values.get(name)]
        if missing:
            # Kopf-, Filter- und Layoutzeilen fallen hier heraus.
            return None

        detail_raw = values.get("detail_url")
        detail_url = urljoin(self.config.base_url + "/", detail_raw) if detail_raw else None
        source_id = self._source_id(detail_url, values)

        deadline_text = values.get("submission_deadline")
        deadline = self._parse_deadline(deadline_text)
        notes: list[str] = []
        if deadline_text and deadline is None:
            # Nicht raten: die Rohangabe bleibt sichtbar, das Feld leer.
            notes.append(f"Frist nicht lesbar: {deadline_text!r} - bitte am Portal pruefen.")
        elif deadline is not None:
            notes.append(
                f"Frist als Ortszeit ({self.config.timezone}) aus der Trefferliste gelesen - "
                "vor einer Teilnahme gegen die Originalbekanntmachung pruefen."
            )

        cpv_text = values.get("cpv_codes")
        cpv_codes = _CPV.findall(cpv_text) if cpv_text else []
        amount = parse_amount(values.get("estimated_value"))

        extra = {name: value for name, value in values.items() if name not in FIELDS}
        evidence = _clean(row.get_text(" ", strip=True))

        return Tender(
            id=make_tender_id(self.name, source_id),
            source=self.name,
            source_id=source_id,
            source_url=detail_url or page_url,
            title=values.get("title"),
            contracting_authority=values.get("contracting_authority") or self.config.authority,
            description=values.get("description"),
            national_id=values.get("national_id"),
            country=self.config.country,
            region=values.get("region") or self.config.region,
            cpv_codes=cpv_codes,
            notice_type=values.get("notice_type"),
            procedure_type=values.get("procedure_type"),
            publication_date=parse_date(values.get("publication_date")),
            submission_deadline=deadline,
            estimated_value=amount,
            value_is_estimated=amount is not None,
            currency=parse_currency(values.get("currency") or values.get("estimated_value")),
            status=TenderStatus.OPEN,
            notes=notes,
            provenance=Provenance(
                source=self.name,
                source_id=source_id,
                source_url=detail_url or page_url,
                method="html",
                document=self.config.label or self.name,
                original_text=evidence[:1000] if evidence else None,
            ),
            raw={
                "portal": self.config.label or self.name,
                "list_url": page_url,
                "detail_url": detail_url,
                "fields": {name: value for name, value in values.items() if value},
                "extra": {name: value for name, value in extra.items() if value},
            },
        )

    async def _enrich_from_detail(self, tender: Tender) -> None:
        """Detailseite nachladen und leere Felder ergaenzen.

        Ergaenzt wird nur, was in der Liste fehlt - ein Detailwert ueberschreibt
        keine bereits belegte Angabe, damit die Herkunft eindeutig bleibt.
        Schlaegt der Abruf fehl, bleibt der Treffer aus der Liste erhalten.
        """
        if not self.config.detail_fields or not tender.source_url:
            return
        try:
            soup = await self._soup(tender.source_url)
        except RobotsDisallowedError as exc:
            self.log.warning("detail_disallowed_by_robots", url=tender.source_url, error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - ein Detailabruf stoppt nie den Lauf
            self.log.warning("detail_fetch_failed", url=tender.source_url, error=str(exc))
            tender.notes.append(f"Detailseite nicht abrufbar: {exc}")
            return

        body = soup.body or soup
        values = self._read_fields(body, self.config.detail_fields)
        if not tender.description and values.get("description"):
            tender.description = values["description"]
        if not tender.contracting_authority and values.get("contracting_authority"):
            tender.contracting_authority = values["contracting_authority"]
        if not tender.national_id and values.get("national_id"):
            tender.national_id = values["national_id"]
        if not tender.procedure_type and values.get("procedure_type"):
            tender.procedure_type = values["procedure_type"]
        if not tender.notice_type and values.get("notice_type"):
            tender.notice_type = values["notice_type"]
        if not tender.publication_date and values.get("publication_date"):
            tender.publication_date = parse_date(values["publication_date"])
        if not tender.cpv_codes and values.get("cpv_codes"):
            tender.cpv_codes = _CPV.findall(values["cpv_codes"] or "")
        if tender.submission_deadline is None and values.get("submission_deadline"):
            tender.submission_deadline = self._parse_deadline(values["submission_deadline"])
        if tender.estimated_value is None and values.get("estimated_value"):
            amount = parse_amount(values["estimated_value"])
            if amount is not None:
                tender.estimated_value = amount
                tender.value_is_estimated = True
                tender.currency = tender.currency or parse_currency(values["estimated_value"])
        tender.raw["detail_fields"] = {name: value for name, value in values.items() if value}

    # -- Diagnose ------------------------------------------------------------

    async def health_check(self) -> SourceStatus:
        """Liste einmal abrufen und je Feld die Trefferquote melden.

        Das ist der eigentliche Nutzen: ein Feld mit "0/20" zeigt auf einen
        falschen Selektor, bevor ein falsch gelesener Wert in der Datenbank
        landet. Ohne diese Rueckmeldung waere ein leeres Ergebnis nicht von
        einem geaenderten Markup zu unterscheiden.
        """
        started = time.perf_counter()
        url = self._page_url(self.config.first_page)
        try:
            soup = await self._soup(url)
        except Exception as exc:  # noqa: BLE001 - der Health-Check meldet jeden Fehler
            return SourceStatus(
                name=self.name,
                type=self.type_name,
                ok=False,
                message=f"{url}: {type(exc).__name__}: {exc}",
                duration_seconds=time.perf_counter() - started,
            )

        rows = self._rows(soup)
        if not rows:
            return SourceStatus(
                name=self.name,
                type=self.type_name,
                ok=False,
                message=(
                    f"Zeilenselektor {self.config.row_selector!r} findet keine Treffer auf {url}"
                ),
                duration_seconds=time.perf_counter() - started,
            )

        filled = dict.fromkeys(self.config.fields, 0)
        usable = 0
        for row in rows:
            values = self._read_fields(row, self.config.fields)
            for name, value in values.items():
                if value:
                    filled[name] += 1
            if all(values.get(name) for name in self.config.required_fields):
                usable += 1

        empty = [name for name, count in filled.items() if count == 0]
        details = ", ".join(f"{name} {count}/{len(rows)}" for name, count in filled.items())
        message = f"{len(rows)} Zeile(n), davon {usable} verwertbar | {details}"
        if empty:
            message += f" | ohne Treffer: {', '.join(empty)}"
        return SourceStatus(
            name=self.name,
            type=self.type_name,
            # Zeilen allein genuegen nicht: ohne verwertbare Zeile stimmen die
            # Feldselektoren nicht, und die Quelle liefert nichts Brauchbares.
            ok=usable > 0,
            message=message,
            sample_count=usable,
            duration_seconds=time.perf_counter() - started,
        )
