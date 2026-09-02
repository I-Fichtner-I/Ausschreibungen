"""T-24: Fehlerpfade der Quell-Registry."""

from __future__ import annotations

from typing import Any

import pytest

from tender_ai.config import Settings, SourceConfig
from tender_ai.core.http import HttpClient
from tender_ai.sources.base import SearchQuery, TenderSource
from tender_ai.sources.registry import available_types, build_sources, register_source


@pytest.fixture
def http(settings: Settings):
    return HttpClient(settings.http)


def test_available_types_contains_builtins():
    assert {"ted", "rss", "fixture"} <= set(available_types())


def test_unknown_type_is_skipped_not_raised(settings: Settings, http: HttpClient):
    settings.sources["kaputt"] = SourceConfig(type="gibtsnicht", enabled=True, priority=1)
    sources = build_sources(settings, http)
    assert "kaputt" not in {s.name for s in sources}
    assert sources, "die uebrigen Quellen muessen weiterhin gebaut werden"


def test_failing_constructor_is_skipped(settings: Settings, http: HttpClient):
    @register_source
    class BrokenInit(TenderSource):
        type_name = "broken-init"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Konstruktor kaputt")

        async def search(self, query: SearchQuery) -> list:  # pragma: no cover
            return []

    settings.sources["broken"] = SourceConfig(type="broken-init", enabled=True, priority=1)
    sources = build_sources(settings, http)
    assert "broken" not in {s.name for s in sources}


def test_only_filter_reports_unknown_name(settings: Settings, http: HttpClient):
    assert build_sources(settings, http, only=["gibtsnicht"]) == []


def test_only_filter_activates_disabled_source(settings: Settings, http: HttpClient):
    settings.sources["fixture"].enabled = False
    sources = build_sources(settings, http, only=["fixture"])
    assert [s.name for s in sources] == ["fixture"]


def test_sources_are_sorted_by_priority(settings: Settings, http: HttpClient):
    sources = build_sources(settings, http)
    assert [s.priority for s in sources] == sorted(s.priority for s in sources)
