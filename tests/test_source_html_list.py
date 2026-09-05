"""Generischer HTML-Listen-Adapter (Portale ohne API)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tender_ai.config import HtmlListSourceConfig, Settings
from tender_ai.core.errors import SourceError
from tender_ai.core.http import HttpClient
from tender_ai.sources.base import SearchQuery
from tender_ai.sources.html_list import HtmlListSource

BASE_URL = "https://portal.test.invalid"
LIST_URL = f"{BASE_URL}/announcements/list.do?method=show"

LIST_HTML = """<html><body>
<table class="results">
  <tr class="head"><th>Bezeichnung</th><th>Vergabestelle</th><th>Frist</th><th>CPV</th></tr>
  <tr class="row">
    <td><a href="/announcements/detail.do?id=4711">Lieferung von Bueromoebeln</a></td>
    <td>Stadt Musterhausen</td>
    <td>15.10.2036, 10:00 Uhr</td>
    <td>39130000-2</td>
  </tr>
  <tr class="row">
    <td><a href="/announcements/detail.do?id=4712">Wartung von Aufzugsanlagen</a></td>
    <td>Stadtwerke Musterhausen</td>
    <td>offen</td>
    <td>50750000</td>
  </tr>
</table>
</body></html>"""

DETAIL_HTML = """<html><body>
  <div class="description">Rahmenvertrag ueber 200 Arbeitsplaetze.</div>
  <div class="procedure">Oeffentliche Ausschreibung</div>
  <div class="reference">VG-NRW-2026-4711</div>
  <div class="value">180.000,00 EUR</div>
</body></html>"""


def _config(**overrides) -> HtmlListSourceConfig:
    payload: dict = {
        "type": "html_list",
        "label": "Testportal",
        "base_url": BASE_URL,
        "list_url": LIST_URL,
        "country": "DEU",
        "region": "Nordrhein-Westfalen",
        "row_selector": "table.results tr",
        "required_fields": ["title", "detail_url"],
        "id_param": "id",
        "fields": {
            "title": {"selector": "a"},
            "detail_url": {"selector": "a", "attribute": "href"},
            "contracting_authority": {"selector": "td:nth-of-type(2)"},
            "submission_deadline": {"selector": "td:nth-of-type(3)"},
            "cpv_codes": {"selector": "td:nth-of-type(4)"},
        },
    }
    payload.update(overrides)
    return HtmlListSourceConfig.model_validate(payload)


def build_source(settings: Settings, http: HttpClient, **overrides) -> HtmlListSource:
    return HtmlListSource(name="portal", config=_config(**overrides), http=http, settings=settings)


@pytest.fixture
async def http_client(settings: Settings):
    client = HttpClient(settings.http)
    try:
        yield client
    finally:
        await client.aclose()


@respx.mock
async def test_rows_become_tenders(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client)

    tenders = await source.search(SearchQuery(max_results=10))

    assert [tender.title for tender in tenders] == [
        "Lieferung von Bueromoebeln",
        "Wartung von Aufzugsanlagen",
    ]
    first = tenders[0]
    assert first.source_id == "4711"  # stabile ID aus dem Query-Parameter
    assert first.source_url == f"{BASE_URL}/announcements/detail.do?id=4711"
    assert first.contracting_authority == "Stadt Musterhausen"
    assert first.cpv_codes == ["39130000"]
    assert first.country == "DEU"
    assert first.region == "Nordrhein-Westfalen"
    assert first.provenance and first.provenance.method == "html"
    assert first.provenance.original_text  # Zeilentext als Beleg


@respx.mock
async def test_header_row_is_not_a_tender(settings: Settings, http_client: HttpClient):
    """Kopfzeilen haben keinen Link - die Pflichtfelder sortieren sie aus."""
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client)
    assert len(await source.search(SearchQuery(max_results=10))) == 2


@respx.mock
async def test_deadline_is_read_as_local_time(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client)

    deadline = (await source.search(SearchQuery(max_results=10)))[0].submission_deadline

    assert deadline is not None
    assert (deadline.year, deadline.month, deadline.day) == (2036, 10, 15)
    assert (deadline.hour, deadline.minute) == (10, 0)
    assert deadline.tzinfo is not None  # nie naiv gespeichert
    assert deadline.utcoffset() is not None


@respx.mock
async def test_unreadable_deadline_stays_empty_with_a_note(
    settings: Settings, http_client: HttpClient
):
    """ "offen" ist keine Frist - und wird auch nicht zu einer gemacht."""
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client)

    second = (await source.search(SearchQuery(max_results=10)))[1]

    assert second.submission_deadline is None
    assert any("nicht lesbar" in note for note in second.notes)


@respx.mock
async def test_regex_narrows_a_field(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(
        settings,
        http_client,
        fields={
            "title": {"selector": "a"},
            "detail_url": {"selector": "a", "attribute": "href"},
            "submission_deadline": {
                "selector": "td:nth-of-type(3)",
                "regex": r"(\d{2}\.\d{2}\.\d{4})",
            },
        },
    )

    first = (await source.search(SearchQuery(max_results=10)))[0]
    assert first.submission_deadline is not None
    assert first.submission_deadline.hour == 0  # Uhrzeit wurde weggeschnitten


@respx.mock
async def test_detail_page_fills_only_empty_fields(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    respx.get(url__startswith=f"{BASE_URL}/announcements/detail.do").mock(
        return_value=httpx.Response(200, text=DETAIL_HTML)
    )
    source = build_source(
        settings,
        http_client,
        follow_detail=True,
        detail_fields={
            "description": {"selector": "div.description"},
            "procedure_type": {"selector": "div.procedure"},
            "national_id": {"selector": "div.reference"},
            "estimated_value": {"selector": "div.value"},
            "contracting_authority": {"selector": "div.description"},
        },
    )

    first = (await source.search(SearchQuery(max_results=1)))[0]

    assert first.description == "Rahmenvertrag ueber 200 Arbeitsplaetze."
    assert first.procedure_type == "Oeffentliche Ausschreibung"
    assert first.national_id == "VG-NRW-2026-4711"
    assert first.estimated_value == 180000.0
    assert first.currency == "EUR"
    assert first.value_is_estimated is True  # nie als amtlicher Wert
    # Die Liste hatte bereits eine Vergabestelle - das Detail ueberschreibt sie nicht.
    assert first.contracting_authority == "Stadt Musterhausen"


@respx.mock
async def test_detail_requests_are_capped(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    detail = respx.get(url__startswith=f"{BASE_URL}/announcements/detail.do").mock(
        return_value=httpx.Response(200, text=DETAIL_HTML)
    )
    source = build_source(
        settings,
        http_client,
        follow_detail=True,
        max_detail_requests=1,
        detail_fields={"description": {"selector": "div.description"}},
    )

    tenders = await source.search(SearchQuery(max_results=10))

    assert len(tenders) == 2
    assert detail.call_count == 1  # das Portal wird nicht ueberrannt


@respx.mock
async def test_failing_detail_page_keeps_the_hit(settings: Settings, http_client: HttpClient):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    respx.get(url__startswith=f"{BASE_URL}/announcements/detail.do").mock(
        return_value=httpx.Response(500)
    )
    source = build_source(
        settings,
        http_client,
        follow_detail=True,
        detail_fields={"description": {"selector": "div.description"}},
    )

    tenders = await source.search(SearchQuery(max_results=1))

    assert len(tenders) == 1
    assert any("Detailseite nicht abrufbar" in note for note in tenders[0].notes)


@respx.mock
async def test_pagination_stops_at_the_first_empty_page(
    settings: Settings, http_client: HttpClient
):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    page_two = respx.get(url__startswith=f"{LIST_URL}&page=2").mock(
        return_value=httpx.Response(
            200, text="<html><body><table class='results'></table></body></html>"
        )
    )
    page_three = respx.get(url__startswith=f"{LIST_URL}&page=3").mock(
        return_value=httpx.Response(200, text=LIST_HTML)
    )
    source = build_source(settings, http_client, page_param="page", max_pages=3)

    tenders = await source.search(SearchQuery(max_results=50))

    assert len(tenders) == 2
    assert page_two.called
    assert not page_three.called


@respx.mock
async def test_total_failure_is_reported_not_silently_empty(
    settings: Settings, http_client: HttpClient
):
    respx.get(LIST_URL).mock(return_value=httpx.Response(503))
    source = build_source(settings, http_client)
    with pytest.raises(SourceError):
        await source.search(SearchQuery(max_results=10))


@respx.mock
async def test_health_check_reports_field_coverage(settings: Settings, http_client: HttpClient):
    """Der eigentliche Nutzen: ein falscher Selektor zeigt sich als 0/N."""
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(
        settings,
        http_client,
        fields={
            "title": {"selector": "a"},
            "detail_url": {"selector": "a", "attribute": "href"},
            "contracting_authority": {"selector": "td.gibt-es-nicht"},
        },
    )

    status = await source.health_check()

    assert status.ok is True
    assert status.sample_count == 2
    assert "title 2/3" in status.message
    assert "ohne Treffer: contracting_authority" in status.message


@respx.mock
async def test_health_check_fails_on_a_wrong_row_selector(
    settings: Settings, http_client: HttpClient
):
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client, row_selector="table.gibt-es-nicht tr")

    status = await source.health_check()

    assert status.ok is False
    assert "findet keine Treffer" in status.message


@respx.mock
async def test_health_check_fails_when_no_row_is_usable(
    settings: Settings, http_client: HttpClient
):
    """Zeilen allein genuegen nicht - ohne Pflichtfelder ist nichts verwertbar."""
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(
        settings,
        http_client,
        fields={"title": {"selector": "span.gibt-es-nicht"}},
        required_fields=["title"],
    )

    status = await source.health_check()
    assert status.ok is False
    assert status.sample_count == 0


def test_url_without_scheme_is_rejected():
    with pytest.raises(ValueError):
        _config(base_url="portal.test.invalid")


@respx.mock
async def test_id_falls_back_to_the_url_when_no_parameter_matches(
    settings: Settings, http_client: HttpClient
):
    """Die Quell-ID ist nie zufaellig - sonst waere jeder Lauf ein Neufund."""
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=LIST_HTML))
    source = build_source(settings, http_client, id_param="gibt-es-nicht")

    first_run = await source.search(SearchQuery(max_results=10))
    second_run = await source.search(SearchQuery(max_results=10))

    assert first_run[0].source_id == second_run[0].source_id
    assert first_run[0].source_id != first_run[1].source_id
