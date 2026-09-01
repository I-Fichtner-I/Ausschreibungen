"""Toleranten Parsen von Quelldaten.

Bekanntmachungsdaten sind heterogen: mal String, mal Liste, mal ein Dict mit
Sprachcodes; Datumsangaben in mehreren Formaten. Diese Helfer geben im
Zweifelsfall ``None`` zurueck - lieber UNKNOWN als ein erfundener Wert.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y%m%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
)

_PREFERRED_LANGS = ("deu", "de", "eng", "en", "fra", "fr")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def first_text(value: Any, preferred_langs: tuple[str, ...] = _PREFERRED_LANGS) -> str | None:
    """Ersten sinnvollen Textwert aus str/list/dict (mehrsprachig) ziehen."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for lang in preferred_langs:
            if lang in value:
                text = first_text(value[lang], preferred_langs)
                if text:
                    return text
        for item in value.values():
            text = first_text(item, preferred_langs)
            if text:
                return text
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = first_text(item, preferred_langs)
            if text:
                return text
    return None


def all_texts(value: Any) -> list[str]:
    """Alle Textwerte flach einsammeln (z. B. fuer CPV-Listen)."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        text = value.strip()
        if text:
            out.append(text)
    elif isinstance(value, (int, float)):
        out.append(str(value))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(all_texts(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            out.extend(all_texts(item))
    return out


def parse_date(value: Any) -> date | None:
    text = first_text(value)
    if not text:
        return None
    text = text.strip()
    # Zeit-/Offsetanteil abtrennen: "2026-08-20+02:00", "2026-08-20T10:00:00Z"
    core = re.split(r"[T ]", text)[0]
    core = re.sub(r"(?<=\d)([+-]\d{2}:?\d{2}|Z)$", "", core)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(core, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    text = first_text(value)
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed_date = parse_date(text)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, datetime.min.time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_amount(value: Any) -> float | None:
    """Geldbetrag aus Zahl, String oder {'amount': ..} extrahieren."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "netAmount", "total"):
            if key in value:
                return parse_amount(value[key])
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            amount = parse_amount(item)
            if amount is not None:
                return amount
        return None
    text = str(value)
    match = _NUM.search(text.replace(".", "").replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


_CURRENCY = re.compile(r"\b(EUR|USD|CHF|GBP|PLN|CZK|DKK|SEK|NOK)\b", re.IGNORECASE)


def parse_currency(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("currency", "currencyCode", "cur"):
            if key in value:
                return parse_currency(value[key])
    text = first_text(value)
    if not text:
        return None
    match = _CURRENCY.search(text)
    if match:
        return match.group(1).upper()
    if len(text.strip()) == 3 and text.strip().isalpha():
        return text.strip().upper()
    if "€" in text:
        return "EUR"
    return None


_TAG = re.compile(r"<[^>]+>")


def strip_html(value: str | None) -> str | None:
    """HTML-Tags entfernen - fuer RSS-Beschreibungen ausreichend."""
    if not value:
        return None
    text = _TAG.sub(" ", value)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", text).strip() or None
