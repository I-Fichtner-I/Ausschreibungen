from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from tender_ai.config import Settings
from tender_ai.core.http import HttpClient
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.models.tender import Tender
from tender_ai.pipeline.ingest import IngestService
from tender_ai.sources.base import SearchQuery, TenderSource
from tender_ai.sources.registry import build_sources

TED_URL = "https://api.test.invalid/v3/notices/search"
FEED_URL = "https://feed.test.invalid/rss.xml"

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Lieferung von 2.000 Monitoren</title>
    <link>https://portal.test.invalid/1</link>
    <guid>portal-1</guid>
    <description>Beschaffung fuer Verwaltungsarbeitsplaetze</description>
    <pubDate>Tue, 25 Aug 2026 08:00:00 +0200</pubDate>
  </item>
</channel></rss>
"""

TED_NOTICE = {
    "publication-number": "00123456-2026",
    "notice-title": {"deu": ["Lieferung von 2.000 Monitoren"]},
    "buyer-name": {"deu": ["Musterstadt"]},
    "buyer-country": "DEU",
    "publication-date": "2026-08-25+02:00",
    "deadline-receipt-request": "2036-09-15T12:00:00+02:00",
    "classification-cpv": ["30231300"],
    "total-value": {"amount": 420000, "currency": "EUR"},
    "links": {"html": {"DEU": "https://ted.test.invalid/00123456-2026"}},
}


class BrokenSource(TenderSource):
    type_name = "broken"

    async def search(self, query: SearchQuery) -> list[Tender]:
        raise RuntimeError("Quelle nicht erreichbar")


@pytest.fixture
async def http_client(settings: Settings):
    client = HttpClient(settings.http)
    client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]
    try:
        yield client
    finally:
        await client.aclose()


@respx.mock
async def test_full_run_stores_results(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [TED_NOTICE]}))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    with session_scope(settings.database_url) as session:
        sources = build_sources(settings, http_client)
        service = IngestService(settings, sources, http_client, session=session)
        report = await service.run(SearchQuery(max_results=10))

        assert report.found == 4  # 1 TED + 1 Feed + 2 Fixture
        assert report.new + report.duplicates == 4
        assert report.errors == []
        repository = TenderRepository(session, settings.dedup)
        assert repository.count(only_primary=False) == 4
        assert len(repository.last_runs()) == 1


@respx.mock
async def test_broken_source_does_not_stop_run(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [TED_NOTICE]}))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    with session_scope(settings.database_url) as session:
        sources = build_sources(settings, http_client)
        sources.append(
            BrokenSource(
                name="broken",
                config=settings.sources["fixture"],
                http=http_client,
                settings=settings,
            )
        )
        service = IngestService(settings, sources, http_client, session=session)
        report = await service.run(SearchQuery(max_results=10))

        assert report.found == 4
        assert len(report.errors) == 1
        assert "Quelle nicht erreichbar" in report.errors[0]["error"]
        states = {s.name: s for s in TenderRepository(session, settings.dedup).source_states()}
        assert states["broken"].consecutive_failures == 1
        assert states["ted"].last_error is None


@respx.mock
async def test_second_run_detects_unchanged_and_updated(
    settings: Settings, http_client: HttpClient
):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [TED_NOTICE]}))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))

    with session_scope(settings.database_url) as session:
        sources = build_sources(settings, http_client, only=["ted"])
        service = IngestService(settings, sources, http_client, session=session)
        first = await service.run(SearchQuery(max_results=10))
        assert first.new == 1

        second = await service.run(SearchQuery(max_results=10))
        assert second.new == 0 and second.updated == 0

        changed = dict(TED_NOTICE, **{"deadline-receipt-request": "2036-10-20T12:00:00+02:00"})
        respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [changed]}))
        third = await service.run(SearchQuery(max_results=10))
        assert third.updated == 1
        assert third.updated_tender_ids == ["ted:00123456-2026"]


@respx.mock
async def test_no_store_mode_keeps_database_empty(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [TED_NOTICE]}))
    sources = build_sources(settings, http_client, only=["ted"])
    service = IngestService(settings, sources, http_client, session=None)
    report = await service.run(SearchQuery(max_results=10), store=False)
    assert report.found == 1
    assert report.stored is False

    with session_scope(settings.database_url) as session:
        assert TenderRepository(session, settings.dedup).count(only_primary=False) == 0


async def test_search_query_from_config(settings: Settings):
    query = SearchQuery.from_config(settings.search)
    assert query.countries == ["DEU"]
    assert query.max_results == 50
    assert query.published_after is not None


def test_query_matches_is_tolerant_about_missing_fields():
    query = SearchQuery(
        keywords=["monitor"],
        countries=["DEU"],
        published_after=date(2026, 1, 1),
        deadline_after=datetime(2026, 9, 1, tzinfo=UTC),
    )
    tender = Tender(id="x:1", source="x", source_id="1", title="Monitor-Lieferung")
    assert query.matches(tender) is True  # fehlende Angaben schliessen nicht aus

    other_country = Tender(
        id="x:2", source="x", source_id="2", title="Monitor-Lieferung", country="FRA"
    )
    assert query.matches(other_country) is False

    wrong_topic = Tender(id="x:3", source="x", source_id="3", title="Strassenbau")
    assert query.matches(wrong_topic) is False


# --- T-01: ein defekter Datensatz kostet nur sich selbst ------------------------
class ThreeSource(TenderSource):
    type_name = "three"

    async def search(self, query: SearchQuery) -> list[Tender]:
        return [
            Tender(id="three:ok", source="three", source_id="ok", title="OK"),
            Tender(id="three:bad", source="three", source_id="bad", title="BAD"),
            Tender(id="three:ok2", source="three", source_id="ok2", title="OK2"),
        ]


async def test_failed_upsert_keeps_other_records(settings: Settings, http_client: HttpClient):
    with session_scope(settings.database_url) as session:
        source = ThreeSource("three", settings.sources["fixture"], http_client, settings)
        service = IngestService(settings, [source], http_client, session=session)
        original = service.repository.upsert

        def flaky(tender: Tender):
            if tender.source_id == "bad":
                raise RuntimeError("simulierter Persistenzfehler")
            return original(tender)

        service.repository.upsert = flaky  # type: ignore[method-assign]
        report = await service.run(SearchQuery(max_results=10))

        assert report.new == 2
        assert report.failed == 1
        assert report.sources[0].failed_ids == ["three:bad"]
        assert [e["tender_id"] for e in report.errors] == ["three:bad"]
        repository = TenderRepository(session, settings.dedup)
        assert {r.id for r in repository.list_tenders(only_primary=False)} == {
            "three:ok",
            "three:ok2",
        }
        run = repository.last_runs(1)[0]
        assert run.new == 2 and any(e.get("tender_id") == "three:bad" for e in run.errors)
