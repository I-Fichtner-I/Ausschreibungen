from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from pydantic import SecretStr

from tender_ai.config import Settings
from tender_ai.core.errors import SourceError
from tender_ai.core.http import HttpClient
from tender_ai.models.tender import DocumentAccess
from tender_ai.sources.base import SearchQuery
from tender_ai.sources.ted import TedSource

TED_URL = "https://api.test.invalid/v3/notices/search"


def _notice(publication_number: str = "00123456-2026") -> dict:
    """Antwortstruktur nach dem Muster der TED-Such-API (eForms-Felder)."""
    return {
        "publication-number": publication_number,
        "notice-title": {"deu": ["Lieferung von 2.000 Monitoren"]},
        "description-lot": {"deu": ["<p>27 Zoll, IPS-Panel</p>"]},
        "buyer-name": {"deu": ["Musterstadt - Zentrale Vergabestelle"]},
        "buyer-country": "DEU",
        "publication-date": "2026-08-25+02:00",
        "deadline-receipt-request": "2036-09-15T12:00:00+02:00",
        "classification-cpv": ["30231300", "30200000"],
        "notice-type": "cn-standard",
        "procedure-type": "open",
        "total-value": {"amount": 420000, "currency": "EUR"},
        "links": {
            "html": {"DEU": "https://ted.test.invalid/notice/00123456-2026"},
            "pdf": {"DEU": "https://ted.test.invalid/notice/00123456-2026.pdf"},
        },
    }


def build_source(settings: Settings, http: HttpClient) -> TedSource:
    return TedSource(name="ted", config=settings.sources["ted"], http=http, settings=settings)


@pytest.fixture
async def http_client(settings: Settings):
    client = HttpClient(settings.http)
    try:
        yield client
    finally:
        await client.aclose()


def test_expert_query_builder(settings: Settings):
    source = build_source(settings, HttpClient(settings.http))
    query = SearchQuery(
        keywords=["Monitore", "Displays"],
        cpv_codes=["30231300"],
        countries=["DEU"],
        published_after=date(2026, 8, 1),
        deadline_after=datetime(2026, 9, 5, tzinfo=UTC),
    )
    built = source.build_expert_query(query)
    assert 'FT ~ ("Monitore")' in built
    assert 'FT ~ ("Displays")' in built
    assert "classification-cpv IN (30231300)" in built
    assert "buyer-country IN (DEU)" in built
    assert "publication-date >= 20260801" in built
    assert "deadline-receipt-request >= 20260905" in built
    assert built.count(" AND ") == 4


def test_expert_query_uses_raw_query_override(settings: Settings):
    settings.sources["ted"].raw_query = "classification-cpv IN (30200000)"
    source = build_source(settings, HttpClient(settings.http))
    assert source.build_expert_query(SearchQuery()) == "classification-cpv IN (30200000)"


@respx.mock
async def test_search_maps_notice_to_tender(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(
        return_value=httpx.Response(200, json={"notices": [_notice()], "totalNoticeCount": 1})
    )
    source = build_source(settings, http_client)
    results = await source.search(SearchQuery(max_results=10))

    assert len(results) == 1
    tender = results[0]
    assert tender.id == "ted:00123456-2026"
    assert tender.source_id == "00123456-2026"
    assert tender.title == "Lieferung von 2.000 Monitoren"
    assert tender.contracting_authority == "Musterstadt - Zentrale Vergabestelle"
    assert tender.description == "27 Zoll, IPS-Panel"  # HTML entfernt
    assert tender.country == "DEU"
    assert tender.cpv_codes == ["30231300", "30200000"]
    assert tender.publication_date == date(2026, 8, 25)
    assert tender.submission_deadline.year == 2036
    assert tender.estimated_value == 420000
    assert tender.currency == "EUR"
    assert tender.source_url == "https://ted.test.invalid/notice/00123456-2026"
    assert [d.access for d in tender.documents] == [DocumentAccess.PUBLIC]
    assert tender.provenance is not None and tender.provenance.method == "api"
    assert tender.raw["publication-number"] == "00123456-2026"


@respx.mock
async def test_search_paginates_until_limit(settings: Settings, http_client: HttpClient):
    page1 = [_notice(f"0000000{i}-2026") for i in range(10)]
    page2 = [_notice(f"0000001{i}-2026") for i in range(5)]
    route = respx.post(TED_URL).mock(
        side_effect=[
            httpx.Response(200, json={"notices": page1}),
            httpx.Response(200, json={"notices": page2}),
        ]
    )
    source = build_source(settings, http_client)
    results = await source.search(SearchQuery(max_results=20))
    assert len(results) == 15
    assert route.call_count == 2


@respx.mock
async def test_search_skips_notices_without_id(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(
        return_value=httpx.Response(200, json={"notices": [{"notice-title": "ohne Nummer"}]})
    )
    source = build_source(settings, http_client)
    assert await source.search(SearchQuery(max_results=5)) == []


@respx.mock
async def test_unexpected_structure_raises_source_error(
    settings: Settings, http_client: HttpClient
):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"unexpected": {}}))
    source = build_source(settings, http_client)
    with pytest.raises(SourceError):
        await source.search(SearchQuery(max_results=5))


@respx.mock
async def test_api_key_is_sent_as_header(settings: Settings, http_client: HttpClient):
    settings.ted_api_key = SecretStr("geheim")
    settings.sources["ted"].auth_scheme = "ApiKey"
    route = respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": []}))
    source = build_source(settings, http_client)
    await source.search(SearchQuery(max_results=1))
    assert route.calls[0].request.headers["Authorization"] == "ApiKey geheim"


@respx.mock
async def test_health_check_reports_failure(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(return_value=httpx.Response(500))
    http_client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]
    source = build_source(settings, http_client)
    status = await source.health_check()
    assert status.ok is False
    assert "HttpError" in status.message


# --- T-04: generische Quell-Keys ------------------------------------------------
@respx.mock
async def test_source_api_keys_take_precedence(settings: Settings, http_client: HttpClient):
    settings.ted_api_key = SecretStr("alt")
    settings.source_api_keys = {"ted": SecretStr("neu")}
    settings.sources["ted"].auth_scheme = "ApiKey"
    route = respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": []}))
    await build_source(settings, http_client).search(SearchQuery(max_results=1))
    assert route.calls[0].request.headers["Authorization"] == "ApiKey neu"


# --- T-07: Status aus notice-type ----------------------------------------------
@pytest.mark.parametrize(
    ("notice_type", "deadline", "expected"),
    [
        ("cn-standard", "2036-09-15T12:00:00+02:00", "OPEN"),
        ("cn-standard", "2020-01-01T12:00:00+02:00", "CLOSED"),
        ("can-standard", None, "AWARDED"),
        ("corr", None, "AMENDED"),
        (None, None, "UNKNOWN"),
        (None, "2036-09-15T12:00:00+02:00", "OPEN"),
        ("voelliger-unsinn", None, "UNKNOWN"),
    ],
)
@respx.mock
async def test_status_is_derived_from_notice_type(
    settings: Settings, http_client: HttpClient, notice_type, deadline, expected
):
    notice = _notice()
    notice["notice-type"] = notice_type
    if deadline is None:
        notice.pop("deadline-receipt-request")
    else:
        notice["deadline-receipt-request"] = deadline
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [notice]}))
    (tender,) = await build_source(settings, http_client).search(SearchQuery(max_results=1))
    assert str(tender.status) == expected


@respx.mock
async def test_status_map_is_configurable(settings: Settings, http_client: HttpClient):
    settings.sources["ted"].status_map = {"xyz-": "CANCELLED"}
    notice = _notice()
    notice["notice-type"] = "xyz-special"
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [notice]}))
    (tender,) = await build_source(settings, http_client).search(SearchQuery(max_results=1))
    assert str(tender.status) == "CANCELLED"


# --- T-05 / T-24: Dokumentendownload -------------------------------------------
@respx.mock
async def test_download_documents_writes_under_documents_dir(
    settings: Settings, http_client: HttpClient, tmp_path
):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [_notice()]}))
    respx.get("https://ted.test.invalid/notice/00123456-2026.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 demo")
    )
    source = build_source(settings, http_client)
    (tender,) = await source.search(SearchQuery(max_results=1))
    downloaded = await source.download_documents(tender, tmp_path / "docs")
    assert len(downloaded) == 1
    local = downloaded[0].local_path
    assert local is not None
    assert (tmp_path / "docs").resolve() in __import__("pathlib").Path(local).resolve().parents
    assert __import__("pathlib").Path(local).read_bytes() == b"%PDF-1.4 demo"
    assert downloaded[0].retrieved_at is not None


@respx.mock
async def test_download_failure_is_noted_not_raised(
    settings: Settings, http_client: HttpClient, tmp_path
):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [_notice()]}))
    respx.get("https://ted.test.invalid/notice/00123456-2026.pdf").mock(
        return_value=httpx.Response(404)
    )
    http_client._sleep = lambda _s: __import__("asyncio").sleep(0)  # type: ignore[assignment]
    source = build_source(settings, http_client)
    (tender,) = await source.search(SearchQuery(max_results=1))
    assert await source.download_documents(tender, tmp_path / "docs") == []
    assert tender.documents[0].note and "fehlgeschlagen" in tender.documents[0].note


@respx.mock
async def test_get_tender_details(settings: Settings, http_client: HttpClient):
    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": [_notice()]}))
    tender = await build_source(settings, http_client).get_tender_details("ted:00123456-2026")
    assert tender is not None and tender.source_id == "00123456-2026"

    respx.post(TED_URL).mock(return_value=httpx.Response(200, json={"notices": []}))
    assert await build_source(settings, http_client).get_tender_details("ted:x") is None
