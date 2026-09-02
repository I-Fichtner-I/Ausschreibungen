"""T-22: der Service-Layer, den CLI und spaeteres Dashboard gemeinsam nutzen."""

from __future__ import annotations

import httpx
import pytest
import respx

from tender_ai.config import Settings
from tender_ai.core.errors import ConfigError
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.services import check_sources, run_search
from tender_ai.services.search import source_names
from tender_ai.sources.base import SearchQuery

TED_URL = "https://api.test.invalid/v3/notices/search"
FEED_URL = "https://feed.test.invalid/rss.xml"


async def test_run_search_stores_results(settings: Settings):
    report = await run_search(settings, SearchQuery(max_results=10), only_sources=["fixture"])
    assert report.found == 2
    assert report.new == 2
    with session_scope(settings.database_url) as session:
        assert TenderRepository(session, settings.dedup).count(only_primary=False) == 2


async def test_run_search_without_store_leaves_db_empty(settings: Settings):
    report = await run_search(
        settings, SearchQuery(max_results=10), only_sources=["fixture"], store=False
    )
    assert report.found == 2
    assert report.stored is False
    with session_scope(settings.database_url) as session:
        assert TenderRepository(session, settings.dedup).count(only_primary=False) == 0


async def test_run_search_without_sources_raises_config_error(settings: Settings):
    with pytest.raises(ConfigError, match="Keine aktive Quelle"):
        await run_search(settings, SearchQuery(), only_sources=["gibtsnicht"])


@respx.mock
async def test_run_search_reports_broken_source(settings: Settings):
    respx.post(TED_URL).mock(return_value=httpx.Response(500))
    respx.get(FEED_URL).mock(return_value=httpx.Response(500))
    report = await run_search(settings, SearchQuery(max_results=5))
    # fixture liefert Treffer, ted und feed melden Fehler - der Lauf laeuft weiter
    assert report.found == 2
    assert {e["source"] for e in report.source_errors} == {"ted", "feed"}


@respx.mock
async def test_check_sources_reports_each_source(settings: Settings):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": []}))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text="<rss/>"))
    statuses = {status.name: status for status in await check_sources(settings)}
    assert set(statuses) == {"ted", "feed", "fixture"}
    assert statuses["ted"].ok is True
    assert statuses["fixture"].ok is True


async def test_check_sources_with_unknown_name_returns_empty(settings: Settings):
    assert await check_sources(settings, ["gibtsnicht"]) == []


def test_source_names_respects_priority_and_selection(settings: Settings):
    assert source_names(settings) == ["ted", "feed", "fixture"]
    assert source_names(settings, ["fixture"]) == ["fixture"]
