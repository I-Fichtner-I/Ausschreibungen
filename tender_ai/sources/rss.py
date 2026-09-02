"""Generischer RSS-/Atom-Adapter fuer Vergabeportale.

Viele Vergabeportale (u. a. service.bund.de) veroeffentlichen ihre
Bekanntmachungen als frei zugaenglichen Feed. Das ist der einfachste
rechtmaessige Weg, ohne HTML-Scraping an neue Ausschreibungen zu kommen.

Weitere Feeds werden in config.yaml unter ``sources.<name>.feeds`` ergaenzt -
ohne Codeaenderung. Der Abruf respektiert robots.txt und das Rate-Limit.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import feedparser

from ..config import FeedConfig, RssSourceConfig
from ..core.errors import HttpError, RobotsDisallowedError, SourceError
from ..models.common import Provenance
from ..models.tender import Tender, TenderStatus, make_tender_id
from .base import SearchQuery, SourceStatus, TenderSource
from .parsing import parse_date, strip_html
from .registry import register_source

# Konservative Erkennung von Fristen im Feed-Text. Die drei Fristarten werden
# getrennt erkannt: nur eine ausdrueckliche Angebots-/Abgabefrist wird zur
# ``submission_deadline``; Binde- und Lieferfrist landen in ihren eigenen
# Feldern. Ein generisches "frist" wird bewusst NICHT akzeptiert.
_DATE = r"[^0-9]{0,40}(\d{1,2}\.\d{1,2}\.\d{4})"
_DEADLINE_PATTERNS: dict[str, re.Pattern[str]] = {
    "submission": re.compile(
        r"(?:angebotsfrist|abgabefrist|einreichungsfrist|teilnahmefrist|bewerbungsfrist"
        r"|schlusstermin|angebotsabgabe\s+bis|abgabe\s+bis)" + _DATE,
        re.IGNORECASE,
    ),
    "binding": re.compile(r"(?:bindefrist|zuschlagsfrist)" + _DATE, re.IGNORECASE),
    "delivery": re.compile(
        r"(?:lieferfrist|ausfuehrungsfrist|ausführungsfrist|leistungszeitraum|liefertermin)"
        + _DATE,
        re.IGNORECASE,
    ),
}

_KIND_LABELS = {
    "submission": "Angebotsfrist",
    "binding": "Bindefrist",
    "delivery": "Lieferfrist",
}


@register_source
class RssSource(TenderSource):
    type_name = "rss"

    config: RssSourceConfig

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.feeds: list[FeedConfig] = list(self.config.feeds)
        for feed in self.feeds:
            self._register_rate_limit(feed.url)

    async def search(self, query: SearchQuery) -> list[Tender]:
        results: list[Tender] = []
        failures: list[str] = []
        usable_feeds = 0

        for feed in self.feeds:
            url = feed.url.strip()
            if not url:
                continue
            usable_feeds += 1
            try:
                tenders = await self._fetch_feed(feed, url)
            except RobotsDisallowedError as exc:
                # Kein Umgehen: Feed wird uebersprungen und protokolliert.
                self.log.warning("feed_disallowed_by_robots", url=url, error=str(exc))
                failures.append(f"{url}: durch robots.txt untersagt")
                continue
            except Exception as exc:  # noqa: BLE001 - ein defekter Feed darf die anderen nicht stoppen
                self.log.error("feed_fetch_failed", url=url, error=str(exc))
                failures.append(f"{url}: {exc}")
                continue

            for tender in tenders:
                if query.matches(tender):
                    results.append(tender)
                if len(results) >= query.max_results:
                    return results

        # Ein einzelner defekter Feed ist verkraftbar; faellt jedoch jeder Feed
        # aus, waere ein stilles "0 Treffer" irrefuehrend - dann meldet die
        # Quelle einen Fehler und der Lauf weist sie als gestoert aus.
        if failures and len(failures) == usable_feeds:
            raise SourceError(self.name, "; ".join(failures))
        return results

    async def health_check(self) -> SourceStatus:
        """Jeden konfigurierten Feed einzeln pruefen."""
        started = time.perf_counter()
        if not self.feeds:
            return SourceStatus(
                name=self.name,
                type=self.type_name,
                ok=False,
                message="Keine Feeds konfiguriert (sources.<name>.feeds)",
            )
        messages: list[str] = []
        total = 0
        reachable = 0
        for feed in self.feeds:
            url = feed.url.strip()
            name = feed.name or url
            if not url:
                messages.append(f"{name}: keine URL konfiguriert")
                continue
            try:
                entries = await self._fetch_feed(feed, url)
            except Exception as exc:  # noqa: BLE001 - Health-Check meldet jeden Fehler, statt abzubrechen
                messages.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            reachable += 1
            total += len(entries)
            messages.append(f"{name}: {len(entries)} Eintraege")
        return SourceStatus(
            name=self.name,
            type=self.type_name,
            ok=reachable > 0,
            message=" | ".join(messages),
            sample_count=total,
            duration_seconds=time.perf_counter() - started,
        )

    async def _fetch_feed(self, feed: FeedConfig, url: str) -> list[Tender]:
        response = await self.http.get(url)
        limit = self.http.config.max_feed_bytes
        if limit and len(response.content) > limit:
            # Uebergrosse Feeds gar nicht erst parsen: der XML-Parser waere die
            # Stelle, an der ein aufgeblaehter Feed teuer wird.
            raise SourceError(
                self.name,
                f"{url}: Feed ist groesser als max_feed_bytes ({limit} Bytes) "
                f"und wird nicht geparst",
            )
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise HttpError(url, f"Feed nicht parsebar: {parsed.bozo_exception}")
        feed_name = feed.name or url
        country = feed.country
        authority = feed.authority
        return [
            self._to_tender(entry, url, feed_name, country, authority) for entry in parsed.entries
        ]

    def _to_tender(
        self,
        entry: Any,
        feed_url: str,
        feed_name: str,
        country: str | None,
        authority: str | None,
    ) -> Tender:
        link = getattr(entry, "link", None) or feed_url
        raw_id = getattr(entry, "id", None) or getattr(entry, "guid", None) or link
        source_id = hashlib.sha256(str(raw_id).encode()).hexdigest()[:24]
        title = strip_html(getattr(entry, "title", None))
        description = strip_html(
            getattr(entry, "summary", None) or getattr(entry, "description", None)
        )

        publication_date: date | None = None
        published_struct: time.struct_time | None = getattr(
            entry, "published_parsed", None
        ) or getattr(entry, "updated_parsed", None)
        if published_struct is not None:
            publication_date = datetime(
                published_struct.tm_year,
                published_struct.tm_mon,
                published_struct.tm_mday,
                published_struct.tm_hour,
                published_struct.tm_min,
                published_struct.tm_sec,
                tzinfo=UTC,
            ).date()
        else:
            publication_date = parse_date(getattr(entry, "published", None))

        found = self._extract_dates(title, description)
        submission = found.get("submission")
        binding = found.get("binding")
        delivery = found.get("delivery")

        notes: list[str] = []
        if found:
            labels = ", ".join(_KIND_LABELS[kind] for kind in found)
            notes.append(
                f"{labels} aus dem Feed-Text extrahiert - vor einer Teilnahme gegen die "
                "Originalbekanntmachung pruefen."
            )

        return Tender(
            id=make_tender_id(self.name, source_id),
            source=self.name,
            source_id=source_id,
            source_url=link,
            title=title,
            contracting_authority=authority,
            description=description,
            country=country,
            publication_date=publication_date,
            submission_deadline=(
                datetime.combine(submission[0], datetime.min.time(), tzinfo=UTC)
                if submission
                else None
            ),
            binding_period_end=binding[0] if binding else None,
            delivery_deadline=delivery[0] if delivery else None,
            status=TenderStatus.OPEN,
            notes=notes,
            provenance=Provenance(
                source=self.name,
                source_id=source_id,
                source_url=link,
                method="rss",
                document=feed_name,
                original_text=submission[1] if submission else None,
            ),
            raw={
                "feed": feed_name,
                "feed_url": feed_url,
                "title": getattr(entry, "title", None),
                "summary": getattr(entry, "summary", None),
                "link": link,
                "published": getattr(entry, "published", None),
                "extracted_dates": {kind: text for kind, (_d, text) in found.items()},
            },
        )

    @staticmethod
    def _extract_dates(*texts: str | None) -> dict[str, tuple[date, str]]:
        """Fristen je Art aus den Texten ziehen; der erste Treffer je Art gewinnt."""
        found: dict[str, tuple[date, str]] = {}
        for kind, pattern in _DEADLINE_PATTERNS.items():
            for text in texts:
                if not text or kind in found:
                    continue
                match = pattern.search(text)
                if not match:
                    continue
                parsed = parse_date(match.group(1))
                if parsed is not None:
                    found[kind] = (parsed, match.group(0))
        return found
