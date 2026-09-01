"""Toleranten Parsen von Quelldaten.

Bekanntmachungsdaten sind heterogen: mal String, mal Liste, mal ein Dict mit
Sprachcodes; Datumsangaben in mehreren Formaten. Diese Helfer geben im
Zweifelsfall ``None`` zurueck - lieber UNKNOWN als ein erfundener Wert.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
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
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def first_text(value: Any, preferred_langs: tuple[str, ...] = _PREFERRED_LANGS) -> str | None:
    """Ersten sinnvollen Textwert aus str/list/dict (mehrsprachig) ziehen."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        for lang in preferred_langs:
            if lang in value:
                preferred = first_text(value[lang], preferred_langs)
                if preferred:
                    return preferred
        for item in value.values():
            nested = first_text(item, preferred_langs)
            if nested:
                return nested
        return None
    if isinstance(value, list | tuple | set):
        for item in value:
            element = first_text(item, preferred_langs)
            if element:
                return element
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
    elif isinstance(value, int | float):
        out.append(str(value))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(all_texts(item))
    elif isinstance(value, list | tuple | set):
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
            # Reines Datum ohne Zeitzone - nur der Datumsanteil wird verwendet.
            return datetime.strptime(core, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    text = first_text(value)
    if not text:
        return None
    text = text.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed_date = parse_date(text)
        if parsed_date is None:
            return None
        parsed = datetime.combine(parsed_date, datetime.min.time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# --- Geldbetraege -------------------------------------------------------------
#
# Betraege kommen in deutscher ("1.234,50"), englischer ("1,234.50") und
# ungruppierter ("1234.50") Schreibweise vor. Regel: Kommen beide Trennzeichen
# vor, ist das letzte das Dezimaltrennzeichen. Kommt nur eines vor, entscheidet
# die Ziffernzahl dahinter: 1-2 Ziffern -> Dezimalstellen, mehrere Gruppen
# -> Tausendertrennung, genau eine Dreiergruppe ("1.234") -> mehrdeutig; sie
# wird als Tausendertrennung gelesen und mit reduzierter Konfidenz markiert.

_AMOUNT_TOKEN = re.compile(r"-?\d[\d.,'\s ]*")
_GROUP_SEPARATOR = re.compile(r"(?<=\d)[\s '](?=\d{3}(?!\d))")

CONFIDENCE_EXACT = 100
CONFIDENCE_AMBIGUOUS = 60


def _normalize_decimal(text: str) -> tuple[str | None, int]:
    match = _AMOUNT_TOKEN.search(text)
    if not match:
        return None, 0
    token = match.group(0).strip().rstrip(".,")
    token = _GROUP_SEPARATOR.sub("", token)
    if re.search(r"[\s ]", token):
        # Rest-Leerzeichen trennen keine Dreiergruppen -> nur den ersten Block nehmen
        token = re.split(r"[\s ]", token)[0].rstrip(".,")
    token = token.replace("'", "")

    has_dot, has_comma = "." in token, "," in token
    confidence = CONFIDENCE_EXACT
    if has_dot and has_comma:
        decimal_sep = token[max(token.rfind("."), token.rfind(","))]
        group_sep = "," if decimal_sep == "." else "."
        token = token.replace(group_sep, "").replace(decimal_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = token.split(sep)
        fraction = parts[-1]
        if len(parts) > 2:
            token = token.replace(sep, "")
        elif len(fraction) == 3 and parts[0].lstrip("-"):
            token = token.replace(sep, "")
            confidence = CONFIDENCE_AMBIGUOUS
        else:
            token = token.replace(sep, ".")
    return token, confidence


def parse_amount_with_confidence(value: Any) -> tuple[float | None, int]:
    """Geldbetrag plus Konfidenz (100 eindeutig, 60 mehrdeutig, 0 kein Wert)."""
    if value is None:
        return None, 0
    if isinstance(value, bool):
        return None, 0
    if isinstance(value, int | float):
        return float(value), CONFIDENCE_EXACT
    if isinstance(value, dict):
        for key in ("amount", "value", "netAmount", "total"):
            if key in value:
                return parse_amount_with_confidence(value[key])
        return None, 0
    if isinstance(value, list | tuple | set):
        for item in value:
            amount, confidence = parse_amount_with_confidence(item)
            if amount is not None:
                return amount, confidence
        return None, 0
    token, confidence = _normalize_decimal(str(value))
    if token is None:
        return None, 0
    try:
        return float(token), confidence
    except ValueError:
        return None, 0


def parse_amount(value: Any) -> float | None:
    """Geldbetrag aus Zahl, String oder {'amount': ..} extrahieren."""
    amount, _confidence = parse_amount_with_confidence(value)
    return amount


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
