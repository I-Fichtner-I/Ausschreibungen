from __future__ import annotations

import httpx
import pytest
import respx

from tender_ai.config import Settings
from tender_ai.core.errors import SourceError
from tender_ai.core.http import HttpClient
from tender_ai.sources.base import SearchQuery
from tender_ai.sources.rss import RssSource

FEED_URL = "https://feed.test.invalid/rss.xml"

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Ausschreibungen</title>
    <item>
      <title>Lieferung von Bueroausstattung</title>
      <link>https://portal.test.invalid/ausschreibung/1</link>
      <guid>https://portal.test.invalid/ausschreibung/1</guid>
      <description>Rahmenvertrag ueber Bueromoebel. Angebotsfrist: 15.10.2036</description>
      <pubDate>Tue, 25 Aug 2026 08:00:00 +0200</pubDate>
    </item>
    <item>
      <title>Wartung von Aufzugsanlagen</title>
      <link>https://portal.test.invalid/ausschreibung/2</link>
      <guid>https://portal.test.invalid/ausschreibung/2</guid>
      <description>&lt;p&gt;Wartung von 34 Anlagen&lt;/p&gt;</description>
      <pubDate>Wed, 26 Aug 2026 08:00:00 +0200</pubDate>
    </item>
  </channel>
</rss>
"""


def build_source(settings: Settings, http: HttpClient) -> RssSource:
    return RssSource(name="feed", config=settings.sources["feed"], http=http, settings=settings)


@pytest.fixture
async def http_client(settings: Settings):
    client = HttpClient(settings.http)
    try:
        yield client
    finally:
        await client.aclose()


@respx.mock
async def test_feed_entries_are_mapped(settings: Settings, http_client: HttpClient):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    source = build_source(settings, http_client)
    results = await source.search(SearchQuery(max_results=10))

    assert len(results) == 2
    first = results[0]
    assert first.title == "Lieferung von Bueroausstattung"
    assert first.source == "feed"
    assert first.source_url == "https://portal.test.invalid/ausschreibung/1"
    assert first.country == "DEU"
    assert first.publication_date.isoformat() == "2026-08-25"
    # Frist aus dem Feed-Text extrahiert und als solche gekennzeichnet
    assert first.submission_deadline.date().isoformat() == "2036-10-15"
    assert first.notes and "Feed-Text" in first.notes[0]
    assert first.provenance.method == "rss"
    assert "Angebotsfrist: 15.10.2036" in first.provenance.original_text

    second = results[1]
    assert second.description == "Wartung von 34 Anlagen"  # HTML entfernt
    assert second.submission_deadline is None  # keine Frist im Text
    assert second.notes == []


@respx.mock
async def test_keyword_filter_applies_client_side(settings: Settings, http_client: HttpClient):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    source = build_source(settings, http_client)
    results = await source.search(SearchQuery(keywords=["Aufzug"], max_results=10))
    assert [t.title for t in results] == ["Wartung von Aufzugsanlagen"]


@respx.mock
async def test_ids_are_stable_across_runs(settings: Settings, http_client: HttpClient):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    source = build_source(settings, http_client)
    first_run = await source.search(SearchQuery(max_results=10))
    second_run = await source.search(SearchQuery(max_results=10))
    assert [t.id for t in first_run] == [t.id for t in second_run]


@respx.mock
async def test_all_feeds_failing_raises_source_error(settings: Settings, http_client: HttpClient):
    respx.get(FEED_URL).mock(return_value=httpx.Response(500))
    http_client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]
    source = build_source(settings, http_client)
    # Faellt jeder Feed aus, waere "0 Treffer" irrefuehrend -> Fehler melden.
    with pytest.raises(SourceError):
        await source.search(SearchQuery(max_results=10))


@respx.mock
async def test_one_broken_feed_does_not_lose_the_others(
    settings: Settings, http_client: HttpClient
):
    settings.sources["feed"].feeds = [
        {"name": "kaputt", "url": "https://broken.test.invalid/rss.xml", "country": "DEU"},
        {"name": "ok", "url": FEED_URL, "country": "DEU"},
    ]
    respx.get("https://broken.test.invalid/rss.xml").mock(return_value=httpx.Response(500))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    http_client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]

    source = build_source(settings, http_client)
    results = await source.search(SearchQuery(max_results=10))
    assert len(results) == 2


@respx.mock
async def test_health_check_reports_each_feed(settings: Settings, http_client: HttpClient):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=FEED_XML))
    status = await build_source(settings, http_client).health_check()
    assert status.ok is True
    assert "Testfeed: 2 Eintraege" in status.message

    respx.get(FEED_URL).mock(return_value=httpx.Response(500))
    http_client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]
    failed = await build_source(settings, http_client).health_check()
    assert failed.ok is False
    assert "HttpError" in failed.message


@respx.mock
async def test_robots_disallow_skips_feed(settings: Settings, http_client: HttpClient):
    http_client.config.respect_robots = True
    http_client.robots.enabled = True
    respx.get("https://feed.test.invalid/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /")
    )
    feed_route = respx.get(FEED_URL)
    source = build_source(settings, http_client)
    # Die Sperre wird respektiert (kein Abruf) und als Quellfehler gemeldet -
    # nicht als "keine Treffer" verschleiert.
    with pytest.raises(SourceError, match="robots.txt"):
        await source.search(SearchQuery(max_results=10))
    assert feed_route.call_count == 0


# --- T-03: Fristarten getrennt --------------------------------------------------
@pytest.mark.parametrize(
    ("text", "submission", "binding", "delivery"),
    [
        ("Bindefrist: 01.12.2026", None, "2026-12-01", None),
        ("Lieferfrist: 30.11.2026", None, None, "2026-11-30"),
        ("Angebotsfrist: 15.10.2036", "2036-10-15", None, None),
        (
            "Angebotsfrist 15.10.2036, Bindefrist bis 01.12.2036, Lieferfrist: 15.01.2037",
            "2036-10-15",
            "2036-12-01",
            "2037-01-15",
        ),
        ("Frist: 01.01.2030", None, None, None),  # generisches "Frist" wird nicht geraten
    ],
)
def test_deadline_kinds_are_separated(text, submission, binding, delivery):
    found = RssSource._extract_dates(None, text)
    got = {kind: value[0].isoformat() for kind, value in found.items()}
    assert got.get("submission") == submission
    assert got.get("binding") == binding
    assert got.get("delivery") == delivery


@respx.mock
async def test_binding_and_delivery_land_in_own_fields(settings: Settings, http_client: HttpClient):
    xml = FEED_XML.replace(
        "Rahmenvertrag ueber Bueromoebel. Angebotsfrist: 15.10.2036",
        "Bindefrist: 01.12.2036, Lieferfrist: 15.01.2037",
    )
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=xml))
    first = (await build_source(settings, http_client).search(SearchQuery(max_results=10)))[0]
    assert first.submission_deadline is None
    assert first.binding_period_end.isoformat() == "2036-12-01"
    assert first.delivery_deadline.isoformat() == "2037-01-15"
    assert first.notes and "Bindefrist" in first.notes[0] and "Lieferfrist" in first.notes[0]
    assert first.provenance.original_text is None
