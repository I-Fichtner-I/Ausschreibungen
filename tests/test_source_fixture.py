from __future__ import annotations

import pytest

from tender_ai.config import Settings
from tender_ai.core.errors import SourceError
from tender_ai.core.http import HttpClient
from tender_ai.sources.base import SearchQuery
from tender_ai.sources.fixture import FixtureSource


def build_source(settings: Settings) -> FixtureSource:
    return FixtureSource(
        name="fixture",
        config=settings.sources["fixture"],
        http=HttpClient(settings.http),
        settings=settings,
    )


async def test_fixture_source_loads_tenders(settings: Settings):
    source = build_source(settings)
    results = await source.search(SearchQuery(max_results=10))
    assert {t.source_id for t in results} == {"t-1", "t-2"}
    assert all(t.id.startswith("fixture:") for t in results)


async def test_fixture_source_applies_filters(settings: Settings):
    source = build_source(settings)
    results = await source.search(SearchQuery(keywords=["Monitor"], max_results=10))
    assert [t.source_id for t in results] == ["t-1"]

    cpv_results = await source.search(SearchQuery(cpv_codes=["50750000"], max_results=10))
    assert [t.source_id for t in cpv_results] == ["t-2"]


async def test_missing_file_raises_source_error(settings: Settings, tmp_path):
    settings.sources["fixture"].path = str(tmp_path / "fehlt.json")
    source = build_source(settings)
    with pytest.raises(SourceError):
        await source.search(SearchQuery())


async def test_get_tender_details(settings: Settings):
    source = build_source(settings)
    tender = await source.get_tender_details("t-1")
    assert tender is not None and tender.title.startswith("Lieferung")
    assert await source.get_tender_details("gibt-es-nicht") is None


async def test_health_check_ok(settings: Settings):
    status = await build_source(settings).health_check()
    assert status.ok is True and status.sample_count == 1
