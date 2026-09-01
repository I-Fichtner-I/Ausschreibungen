from __future__ import annotations

from datetime import date, timezone

from tender_ai.sources.parsing import (
    all_texts,
    first_text,
    parse_amount,
    parse_currency,
    parse_date,
    parse_datetime,
    strip_html,
)


def test_first_text_handles_multilingual_structures():
    assert first_text("Titel") == "Titel"
    assert first_text({"deu": ["Deutscher Titel"], "eng": ["English"]}) == "Deutscher Titel"
    assert first_text({"eng": ["English title"]}) == "English title"
    assert first_text([None, "", "Zweiter"]) == "Zweiter"
    assert first_text(None) is None
    assert first_text([]) is None


def test_all_texts_flattens_nested():
    assert all_texts({"a": ["30231300", "30190000"]}) == ["30231300", "30190000"]
    assert all_texts("30231300") == ["30231300"]
    assert all_texts(None) == []


def test_parse_date_formats():
    assert parse_date("2026-08-20") == date(2026, 8, 20)
    assert parse_date("20260820") == date(2026, 8, 20)
    assert parse_date("20.08.2026") == date(2026, 8, 20)
    assert parse_date("2026-08-20+02:00") == date(2026, 8, 20)
    assert parse_date("2026-08-20T10:00:00Z") == date(2026, 8, 20)
    assert parse_date("kein Datum") is None
    assert parse_date(None) is None


def test_parse_datetime_is_timezone_aware():
    parsed = parse_datetime("2026-09-15T12:00:00+02:00")
    assert parsed is not None and parsed.tzinfo is not None
    naive = parse_datetime("2026-09-15")
    assert naive is not None and naive.tzinfo == timezone.utc


def test_parse_amount_and_currency():
    assert parse_amount({"amount": 1234.5, "currency": "EUR"}) == 1234.5
    assert parse_amount("EUR 1.234,50") == 1234.50
    assert parse_amount(None) is None
    assert parse_currency({"currency": "EUR"}) == "EUR"
    assert parse_currency("1.234,50 €") == "EUR"
    assert parse_currency("etwas") is None


def test_strip_html():
    assert strip_html("<p>Hallo <b>Welt</b></p>") == "Hallo Welt"
    assert strip_html(None) is None
