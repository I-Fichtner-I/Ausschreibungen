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
from datetime import datetime, timezone
from typing import Any

import feedparser

from ..core.errors import HttpError, RobotsDisallowedError, SourceError
from ..models.common import Provenance
from ..models.tender import Tender, TenderStatus, make_tender_id
from .base import SearchQuery, SourceStatus, TenderSource
from .parsing import parse_date, strip_html
from .registry import register_source

# Konservative Erkennung einer Angebotsfrist im Feed-Text. Treffer werden als
# aus dem Text extrahiert gekennzeichnet (Provenance mit original_text).
_DEADLINE_PATTERN = re.compile(
    r"(?:angebotsfrist|abgabefrist|einreichungsfrist|teilnahmefrist|frist|schlusstermin)"
    r"[^0-9]{0,40}(\d{1,2}\.\d{1,2}\.\d{4})",
    re.IGNORECASE,
)


@register_source
class RssSource(TenderSource):
    type_name = "rss"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.feeds: list[dict[str, Any]] = list(getattr(self.config, "feeds", None) or [])
        for feed in self.feeds:
            self._register_rate_limit(str(feed.get("url", "")))

    async def search(self, query: SearchQuery) -> list[Tender]:
        results: list[Tender] = []
        failures: list[str] = []
        usable_feeds = 0

        for feed in self.feeds:
            url = str(feed.get("url", "")).strip()
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
            except Exception as exc:  # HttpError und Parserfehler gleichermassen
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
                name=self.name, type=self.type_name, ok=False,
                message="Keine Feeds konfiguriert (sources.<name>.feeds)",
            )
        messages: list[str] = []
        total = 0
        reachable = 0
        for feed in self.feeds:
            url = str(feed.get("url", "")).strip()
            name = str(feed.get("name") or url)
            if not url:
                messages.append(f"{name}: keine URL konfiguriert")
                continue
            try:
                entries = await self._fetch_feed(feed, url)
            except Exception as exc:
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

    async def _fetch_feed(self, feed: dict[str, Any], url: str) -> list[Tender]:
        response = await self.http.get(url)
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise HttpError(url, f"Feed nicht parsebar: {parsed.bozo_exception}")
        feed_name = str(feed.get("name") or url)
        country = feed.get("country")
        authority = feed.get("authority")
        return [
            self._to_tender(entry, url, feed_name, country, authority)
            for entry in parsed.entries
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

        publication_date = None
        published_struct = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if published_struct:
            publication_date = datetime(*published_struct[:6], tzinfo=timezone.utc).date()
        else:
            publication_date = parse_date(getattr(entry, "published", None))

        deadline, deadline_source = self._extract_deadline(title, description)
        notes: list[str] = []
        if deadline is not None:
            notes.append(
                "Angebotsfrist aus dem Feed-Text extrahiert - vor einer Teilnahme "
                "gegen die Originalbekanntmachung pruefen."
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
            submission_deadline=deadline,
            status=TenderStatus.OPEN,
            notes=notes,
            provenance=Provenance(
                source=self.name,
                source_id=source_id,
                source_url=link,
                method="rss",
                document=feed_name,
                original_text=deadline_source,
            ),
            raw={
                "feed": feed_name,
                "feed_url": feed_url,
                "title": getattr(entry, "title", None),
                "summary": getattr(entry, "summary", None),
                "link": link,
                "published": getattr(entry, "published", None),
            },
        )

    @staticmethod
    def _extract_deadline(*texts: str | None) -> tuple[datetime | None, str | None]:
        for text in texts:
            if not text:
                continue
            match = _DEADLINE_PATTERN.search(text)
            if not match:
                continue
            parsed = parse_date(match.group(1))
            if parsed is None:
                continue
            return (
                datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc),
                match.group(0),
            )
        return None, None
