"""Spalten eines Leistungsverzeichnisses erkennen.

Zwei Wege, weil Vergabeunterlagen beides liefern:

* **Mit Kopfzeile** - die Spaltennamen werden gegen bekannte Bezeichnungen
  abgeglichen ("Pos.", "Bezeichnung", "Menge", "ME").
* **Ohne Kopfzeile** - aus PDF-Tabellen kommt oft nur der Zellinhalt. Dann
  entscheidet der Inhalt der Spalte: fortlaufende Ordnungszahlen, kurze
  Einheitenkuerzel, lange Beschreibungstexte.

Die Zuordnung ist bewusst konservativ: eine Tabelle, die nicht sicher als
Leistungsverzeichnis erkennbar ist, wird uebergangen, statt Zahlen aus einer
Terminliste als Mengen auszugeben.
"""

from __future__ import annotations

import re
from enum import StrEnum

from ..models.common import normalize_text
from ..sources.parsing import parse_amount
from .units import looks_like_unit


class ColumnRole(StrEnum):
    POSITION = "POSITION"
    DESCRIPTION = "DESCRIPTION"
    QUANTITY = "QUANTITY"
    UNIT = "UNIT"
    MANUFACTURER = "MANUFACTURER"
    MODEL = "MODEL"
    ARTICLE_NUMBER = "ARTICLE_NUMBER"
    UNIT_PRICE = "UNIT_PRICE"
    TOTAL_PRICE = "TOTAL_PRICE"


#: Rolle -> normalisierte Spaltenueberschriften. Reihenfolge zaehlt nicht,
#: laengere Treffer gewinnen (siehe ``_role_for_header``).
HEADER_ALIASES: dict[ColumnRole, tuple[str, ...]] = {
    ColumnRole.POSITION: (
        "pos",
        "pos nr",
        "position",
        "positionsnummer",
        "lfd nr",
        "lfdnr",
        "nr",
        "ordnungszahl",
        "oz",
        "item",
        "item no",
        "zeile",
    ),
    ColumnRole.DESCRIPTION: (
        "bezeichnung",
        "beschreibung",
        "artikelbezeichnung",
        "artikel",
        "leistung",
        "leistungsbeschreibung",
        "benennung",
        "kurztext",
        "langtext",
        "gegenstand",
        "text",
        "description",
        "produkt",
        "warenbezeichnung",
        "leistungsverzeichnis",
        "titel",
    ),
    ColumnRole.QUANTITY: (
        "menge",
        "anzahl",
        "stueckzahl",
        "stueck",
        "quantity",
        "qty",
        "umfang",
        "bedarf",
        "liefermenge",
        "gesamtmenge",
    ),
    ColumnRole.UNIT: (
        "einheit",
        "me",
        "mengeneinheit",
        "einh",
        "unit",
        "uom",
        "masseinheit",
        "verrechnungseinheit",
    ),
    ColumnRole.MANUFACTURER: ("hersteller", "fabrikat", "marke", "manufacturer", "brand"),
    ColumnRole.MODEL: (
        "typ",
        "modell",
        "type",
        "model",
        "ausfuehrung",
        "typenbezeichnung",
        "fabrikat typ",
    ),
    ColumnRole.ARTICLE_NUMBER: (
        "art nr",
        "artnr",
        "artikelnummer",
        "artikel nr",
        "bestellnummer",
        "bestell nr",
        "sku",
        "gtin",
        "ean",
        "herstellernummer",
        "hersteller nr",
        "referenznummer",
    ),
    ColumnRole.UNIT_PRICE: (
        "ep",
        "einzelpreis",
        "e preis",
        "preis",
        "preis einheit",
        "unit price",
        "einheitspreis",
        "netto einzelpreis",
    ),
    ColumnRole.TOTAL_PRICE: (
        "gp",
        "gesamtpreis",
        "gesamtbetrag",
        "summe",
        "total",
        "gesamt",
        "gesamtsumme",
        "betrag",
    ),
}

_ALIAS_TO_ROLE: dict[str, ColumnRole] = {
    alias: role for role, aliases in HEADER_ALIASES.items() for alias in aliases
}

#: Ordnungszahlen: "1", "1.10", "02.03.0040", "1a".
POSITION_PATTERN = re.compile(r"^\d{1,4}(?:[./-]\d{1,4}){0,4}[a-z]?\.?$", re.IGNORECASE)
#: Reine Zahl (Menge). Waehrungszeichen deuten auf eine Preisspalte hin.
_CURRENCY_HINT = re.compile(r"[€$£]|\bEUR\b", re.IGNORECASE)


def _role_for_header(cell: str) -> ColumnRole | None:
    """Rolle einer Spaltenueberschrift bestimmen; ``None`` wenn unbekannt."""
    key = normalize_text(cell)
    if not key:
        return None
    direct = _ALIAS_TO_ROLE.get(key)
    if direct is not None:
        return direct
    # "Menge in Stueck", "Pos.-Nr.", "Kurztext der Leistung": der laengste
    # enthaltene Alias entscheidet, damit "einzelpreis" nicht als "preis"
    # und "menge" nicht als "me" gelesen wird.
    best: tuple[int, ColumnRole] | None = None
    for alias, role in _ALIAS_TO_ROLE.items():
        if re.search(rf"(?:^|\s){re.escape(alias)}(?:\s|$)", key) and (
            best is None or len(alias) > best[0]
        ):
            best = (len(alias), role)
    return best[1] if best else None


def map_columns(header: list[str] | None) -> dict[ColumnRole, int]:
    """Kopfzeile -> Spaltenindex je Rolle. Erste Zuordnung gewinnt."""
    mapping: dict[ColumnRole, int] = {}
    if not header:
        return mapping
    for index, cell in enumerate(header):
        role = _role_for_header(cell)
        if role is not None and role not in mapping:
            mapping[role] = index
    return mapping


def _column(rows: list[list[str]], index: int) -> list[str]:
    return [row[index].strip() for row in rows if index < len(row) and row[index]]


def infer_columns(rows: list[list[str]]) -> dict[ColumnRole, int]:
    """Spaltenrollen ohne Kopfzeile aus dem Inhalt ableiten.

    PDF-Tabellen verlieren die Kopfzeile haeufig. Erkannt werden nur die drei
    Rollen, die sich am Inhalt zweifelsfrei zeigen: Ordnungszahl, Einheit,
    Beschreibung - plus die Mengenspalte als verbleibende Zahlenspalte.
    """
    mapping: dict[ColumnRole, int] = {}
    if not rows:
        return mapping
    width = max(len(row) for row in rows)
    numeric_candidates: list[tuple[int, float]] = []
    text_candidates: list[tuple[int, float]] = []

    for index in range(width):
        values = _column(rows, index)
        if not values:
            continue
        total = len(values)
        positions = sum(1 for value in values if POSITION_PATTERN.match(value))
        units = sum(1 for value in values if looks_like_unit(value))
        numbers = sum(1 for value in values if parse_amount(value) is not None)
        currency = sum(1 for value in values if _CURRENCY_HINT.search(value))
        average_length = sum(len(value) for value in values) / total

        if ColumnRole.POSITION not in mapping and positions / total >= 0.8 and index <= 1:
            mapping[ColumnRole.POSITION] = index
            continue
        if ColumnRole.UNIT not in mapping and units / total >= 0.8 and average_length <= 8:
            mapping[ColumnRole.UNIT] = index
            continue
        if numbers / total >= 0.8 and not currency:
            numeric_candidates.append((index, average_length))
            continue
        if average_length >= 10:
            text_candidates.append((index, average_length))

    if text_candidates:
        # Die Beschreibung ist die laengste Textspalte.
        mapping[ColumnRole.DESCRIPTION] = max(text_candidates, key=lambda item: item[1])[0]
    if numeric_candidates and ColumnRole.QUANTITY not in mapping:
        # Die Menge steht typischerweise vor den Preisspalten - die erste
        # reine Zahlenspalte ohne Waehrungszeichen ist der beste Kandidat.
        mapping[ColumnRole.QUANTITY] = min(numeric_candidates, key=lambda item: item[0])[0]
    return mapping


def is_item_table(mapping: dict[ColumnRole, int]) -> bool:
    """Beschreibt diese Spaltenbelegung ein Leistungsverzeichnis?

    Verlangt eine Beschreibungsspalte und mindestens eine Angabe, die eine
    Position ausmacht (Menge, Einheit oder Ordnungszahl). Ohne Beschreibung
    ist die Zeile kein Artikel, sondern bestenfalls eine Zahlenreihe.
    """
    if ColumnRole.DESCRIPTION not in mapping:
        return False
    return any(
        role in mapping for role in (ColumnRole.QUANTITY, ColumnRole.UNIT, ColumnRole.POSITION)
    )
