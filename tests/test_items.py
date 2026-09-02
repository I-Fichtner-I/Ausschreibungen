"""Stufe 3: Artikelerkennung aus Leistungsverzeichnissen."""

from __future__ import annotations

import pytest

from tender_ai.items import extract_items, items_from_table, items_from_text
from tender_ai.items.columns import ColumnRole, infer_columns, is_item_table, map_columns
from tender_ai.items.extract import deduplicate
from tender_ai.items.units import looks_like_unit, normalize_unit
from tender_ai.models.document import ExtractedDocument, ExtractedPage, ExtractedTable
from tender_ai.models.item import MAX_CONFIDENCE, ItemSourceKind, TenderItem


def _document(
    *,
    name: str = "Leistungsverzeichnis.pdf",
    tables: list[ExtractedTable] | None = None,
    text: str | None = None,
) -> ExtractedDocument:
    return ExtractedDocument(
        source_path=name,
        file_name=name,
        tables=tables or [],
        pages=[ExtractedPage(number=1, text=text)] if text is not None else [],
    )


def _lv_table(rows: list[list[str]], header: list[str] | None = None) -> ExtractedTable:
    return ExtractedTable(
        page=1,
        header=header if header is not None else ["Pos.", "Bezeichnung", "Menge", "ME"],
        rows=rows,
    )


# --------------------------------------------------------------------------
# Einheiten
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Stk.", "STK"),
        ("Stück", "STK"),
        ("STCK", "STK"),
        ("St", "STK"),
        ("lfm", "M"),
        ("m²", "M2"),
        ("qm", "M2"),
        ("Pauschal", "PSCH"),
        ("psch", "PSCH"),
        ("Std.", "H"),
        ("Karton", "KRT"),
        ("Paar", "PA"),
    ],
)
def test_normalize_unit_maps_common_spellings(raw: str, expected: str):
    canonical, original = normalize_unit(raw)
    assert canonical == expected
    assert original == raw  # der Originaltext geht nie verloren


def test_unknown_unit_is_not_guessed():
    canonical, original = normalize_unit("Zoll")
    assert canonical is None
    assert original == "Zoll"  # bleibt als Beleg erhalten
    assert looks_like_unit("Zoll") is False


def test_empty_unit_stays_empty():
    assert normalize_unit(None) == (None, None)
    assert normalize_unit("   ") == (None, None)


# --------------------------------------------------------------------------
# Spaltenerkennung
# --------------------------------------------------------------------------


def test_map_columns_reads_german_headers():
    mapping = map_columns(["Pos.", "Bezeichnung", "Menge", "ME", "Hersteller", "Typ"])
    assert mapping[ColumnRole.POSITION] == 0
    assert mapping[ColumnRole.DESCRIPTION] == 1
    assert mapping[ColumnRole.QUANTITY] == 2
    assert mapping[ColumnRole.UNIT] == 3
    assert mapping[ColumnRole.MANUFACTURER] == 4
    assert mapping[ColumnRole.MODEL] == 5


def test_longest_header_alias_wins():
    """ "Einzelpreis" darf nicht als "Preis" und "Menge" nicht als "ME" gelten."""
    mapping = map_columns(["Menge in Stueck", "Einzelpreis", "Gesamtpreis"])
    assert mapping[ColumnRole.QUANTITY] == 0
    assert mapping[ColumnRole.UNIT_PRICE] == 1
    assert mapping[ColumnRole.TOTAL_PRICE] == 2
    assert ColumnRole.UNIT not in mapping


def test_infer_columns_without_header():
    rows = [
        ["1", "Buerostuhl drehbar mit Armlehne", "20", "Stk"],
        ["2", "Schreibtisch 160x80 cm hoehenverstellbar", "15", "Stk"],
        ["3", "Rollcontainer mit drei Schubladen", "18", "Stk"],
    ]
    mapping = infer_columns(rows)
    assert mapping[ColumnRole.POSITION] == 0
    assert mapping[ColumnRole.DESCRIPTION] == 1
    assert mapping[ColumnRole.QUANTITY] == 2
    assert mapping[ColumnRole.UNIT] == 3
    assert is_item_table(mapping)


def test_table_without_description_is_no_item_table():
    """Eine Terminliste ist kein Leistungsverzeichnis."""
    mapping = map_columns(["Datum", "Uhrzeit"])
    assert not is_item_table(mapping)
    table = _lv_table([["01.09.2026", "10:00"], ["02.09.2026", "11:00"]], ["Datum", "Uhrzeit"])
    assert items_from_table(table, _document()) == []


def test_price_column_is_not_taken_as_quantity():
    rows = [
        ["Buerostuhl drehbar mit Armlehne", "129,00 EUR"],
        ["Schreibtisch 160x80 cm hoehenverstellbar", "349,00 EUR"],
    ]
    mapping = infer_columns(rows)
    assert ColumnRole.QUANTITY not in mapping


# --------------------------------------------------------------------------
# Tabellenzeilen
# --------------------------------------------------------------------------


def test_items_from_table_reads_position_quantity_and_unit():
    table = _lv_table(
        [
            ["1.10", "Buerostuhl drehbar, Farbe: schwarz", "20", "Stk"],
            ["1.20", "Schreibtisch 160x80 cm", "15", "Stück"],
        ]
    )
    items = items_from_table(table, _document())
    assert [item.position for item in items] == ["1.10", "1.20"]
    assert items[0].quantity == 20.0
    assert items[0].unit == "STK"
    assert items[0].specifications == {"Farbe": "schwarz"}
    assert items[0].source_kind is ItemSourceKind.TABLE
    # Jede Position bleibt auf Dokument und Seite rueckfuehrbar
    assert items[0].provenance.document == "Leistungsverzeichnis.pdf"
    assert items[0].provenance.page == 1
    assert "Buerostuhl" in items[0].provenance.original_text


def test_summary_rows_are_skipped():
    table = _lv_table(
        [
            ["1", "Buerostuhl drehbar", "20", "Stk"],
            ["", "Zwischensumme", "", ""],
            ["", "Gesamtbetrag netto", "", ""],
            ["", "Uebertrag", "", ""],
        ]
    )
    items = items_from_table(table, _document())
    assert [item.title for item in items] == ["Buerostuhl drehbar"]


def test_unreadable_quantity_stays_unknown():
    """Keine Menge ist besser als eine erfundene Menge."""
    table = _lv_table([["1", "Rollcontainer", "auf Abruf", "Stk"], ["2", "Stuhl", "5", "Stk"]])
    items = items_from_table(table, _document())
    assert items[0].quantity is None
    assert items[0].warnings and "auf Abruf" in items[0].warnings[0]
    assert not items[0].is_priceable


def test_approximate_quantity_is_marked_as_estimate():
    table = _lv_table([["1", "Buerostuhl drehbar", "ca. 20", "Stk"], ["2", "Tisch", "5", "Stk"]])
    items = items_from_table(table, _document())
    assert items[0].quantity == 20.0
    assert items[0].quantity_estimated is True
    assert items[1].quantity_estimated is False


def test_ambiguous_thousand_separator_is_flagged():
    table = _lv_table([["1", "Schrauben verzinkt", "1.234", "Stk"], ["2", "Muttern", "50", "Stk"]])
    items = items_from_table(table, _document())
    assert items[0].quantity == 1234.0
    assert items[0].quantity_estimated is True
    assert any("mehrdeutig" in warning for warning in items[0].warnings)


def test_unknown_unit_is_reported():
    table = _lv_table([["1", "Kabel Kategorie 7", "50", "Zoll"], ["2", "Stecker", "50", "Stk"]])
    items = items_from_table(table, _document())
    assert items[0].unit is None
    assert items[0].unit_original == "Zoll"
    assert any("Einheit unbekannt" in warning for warning in items[0].warnings)


def test_brand_lock_only_without_equivalence_clause():
    table = _lv_table(
        [
            ["1", "Monitor, Fabrikat: Muster GmbH, Typ: MX-27", "10", "Stk"],
            ["2", "Drucker, Fabrikat: ACME, Typ: LP-9 oder gleichwertig", "5", "Stk"],
        ]
    )
    items = items_from_table(table, _document())
    assert items[0].brand_locked is True
    assert items[1].brand_locked is False
    # Der Zusatz gehoert nicht in die Typbezeichnung - sonst findet Stufe 4 nichts.
    assert items[1].model_number == "LP-9"


def test_manufacturer_columns_win_over_text():
    table = _lv_table(
        [["1", "Monitor 27 Zoll", "10", "Stk", "Muster GmbH", "MX-27"]],
        ["Pos.", "Bezeichnung", "Menge", "ME", "Hersteller", "Typ"],
    )
    table.rows.append(["2", "Tastatur", "10", "Stk", "ACME", "KB-1"])
    items = items_from_table(table, _document())
    assert items[0].manufacturer == "Muster GmbH"
    assert items[0].model_number == "MX-27"


def test_confidence_grows_with_completeness_and_never_reaches_hundred():
    complete = _lv_table(
        [
            [
                "1.10",
                "Monitor 27 Zoll, Fabrikat: Muster GmbH, Typ: MX-27, Art.-Nr. 4711, Farbe: schwarz",
                "20",
                "Stk",
            ],
            ["1.20", "Tastatur", "5", "Stk"],
        ]
    )
    items = items_from_table(complete, _document())
    assert items[0].confidence > items[1].confidence
    assert items[0].confidence <= MAX_CONFIDENCE


def test_single_row_needs_an_explicit_header():
    """Eine ausgeschriebene LV-Kopfzeile belegt die Tabelle auch bei einer Zeile.

    Ohne Kopfzeile ist die Spaltenbelegung nur geraten - dann waere ein
    einzelner Formularkasten nicht von einem Leistungsverzeichnis zu
    unterscheiden.
    """
    row = [["1", "Buerostuhl drehbar", "20", "Stk"]]
    with_header = _lv_table(row)
    assert len(items_from_table(with_header, _document())) == 1

    without_header = ExtractedTable(page=1, header=None, rows=row)
    assert items_from_table(without_header, _document()) == []


# --------------------------------------------------------------------------
# Fliesstext als Rueckfallebene
# --------------------------------------------------------------------------


def test_text_fallback_needs_position_quantity_and_unit():
    document = _document(
        text=(
            "1. 20 Stk Buerostuhl drehbar mit Armlehne\n"
            "2. Schreibtisch 160x80 cm, 15 Stück, hoehenverstellbar\n"
            "3. Beratungsleistung nach Aufwand\n"
            "Summe 12 Stk Zwischenposten\n"
        )
    )
    items = items_from_text(document)
    assert [item.position for item in items] == ["1", "2"]
    assert items[0].quantity == 20.0 and items[0].unit == "STK"
    assert items[1].title == "Schreibtisch 160x80 cm, hoehenverstellbar"
    assert all(item.source_kind is ItemSourceKind.TEXT for item in items)


def test_text_items_are_less_confident_than_table_items():
    table = _lv_table(
        [["1", "Buerostuhl drehbar mit Armlehne", "20", "Stk"], ["2", "Tisch", "5", "Stk"]]
    )
    table_items = items_from_table(table, _document())
    text_items = items_from_text(_document(text="1. 20 Stk Buerostuhl drehbar mit Armlehne\n"))
    assert text_items[0].confidence < table_items[0].confidence


def test_text_fallback_only_when_no_table_found():
    document = _document(
        tables=[_lv_table([["1", "Buerostuhl drehbar", "20", "Stk"], ["2", "Tisch", "5", "Stk"]])],
        text="9. 99 Stk Sollte nicht erscheinen\n",
    )
    result = extract_items([document], tender_id="t-1")
    assert [item.title for item in result.items] == ["Buerostuhl drehbar", "Tisch"]


def test_text_fallback_result_carries_warning():
    result = extract_items(
        [_document(text="1. 20 Stk Buerostuhl drehbar mit Armlehne\n")], tender_id="t-1"
    )
    assert result.items
    assert any("Fliesstext" in warning for warning in result.warnings)


# --------------------------------------------------------------------------
# Zusammenfuehren und Gesamtergebnis
# --------------------------------------------------------------------------


def test_same_position_from_two_documents_is_merged():
    rows = [["1.10", "Buerostuhl drehbar", "20", "Stk"], ["1.20", "Tisch", "5", "Stk"]]
    pdf = _document(name="LV.pdf", tables=[_lv_table(rows)])
    xlsx = _document(name="Preisblatt.xlsx", tables=[_lv_table(rows)])
    result = extract_items([pdf, xlsx], tender_id="t-1")
    assert result.item_count == 2
    # Die zweite Fundstelle verschwindet nicht spurlos.
    assert any("Preisblatt.xlsx" in warning for warning in result.items[0].warnings)


def test_deduplicate_keeps_the_better_read_entry():
    weak = TenderItem(position="1", title="Buerostuhl", confidence=40)
    strong = TenderItem(position="1", title="Buerostuhl", confidence=80, unit="STK")
    assert deduplicate([weak, strong])[0].confidence == 80
    assert deduplicate([strong, weak])[0].confidence == 80


def test_items_are_sorted_by_position_number():
    table = _lv_table(
        [
            ["1.10", "Buerostuhl", "1", "Stk"],
            ["1.9", "Tisch", "1", "Stk"],
            ["2", "Lampe", "1", "Stk"],
        ]
    )
    result = extract_items([_document(tables=[table])], tender_id="t-1")
    assert [item.position for item in result.items] == ["1.9", "1.10", "2"]


def test_result_reports_counts_and_missing_quantities():
    table = _lv_table(
        [
            ["1", "Buerostuhl drehbar", "20", "Stk"],
            ["2", "Wartung nach Aufwand", "auf Abruf", "Std"],
        ]
    )
    result = extract_items([_document(tables=[table])], tender_id="t-1")
    assert result.item_count == 2
    assert result.priceable_count == 1
    assert result.tables_scanned == 1 and result.tables_used == 1
    assert result.total_quantity_known is False
    assert any("ohne erkannte Menge" in warning for warning in result.warnings)


def test_empty_documents_yield_explicit_warning():
    result = extract_items([], tender_id="t-1")
    assert result.item_count == 0
    assert any("Keine ausgelesenen Unterlagen" in warning for warning in result.warnings)


def test_scan_without_text_is_reported_not_invented():
    result = extract_items([_document(text="")], tender_id="t-1")
    assert result.item_count == 0
    assert any("Scan" in warning for warning in result.warnings)


def test_result_is_truncated_at_the_limit():
    rows = [[str(index), f"Artikel Nummer {index}", "1", "Stk"] for index in range(1, 8)]
    result = extract_items([_document(tables=[_lv_table(rows)])], tender_id="t-1", max_items=3)
    assert result.item_count == 3
    assert any("gekuerzt" in warning for warning in result.warnings)


def test_as_dict_shows_unknown_instead_of_null():
    table = _lv_table([["1", "Wartung", "auf Abruf", "Zoll"], ["2", "Stuhl", "5", "Stk"]])
    payload = extract_items([_document(tables=[table])], tender_id="t-1").as_dict()
    first = payload["items"][0]
    assert first["unit"] == "UNKNOWN"
    assert first["quantity"] is None
    assert first["match_confidence"] is None  # Produktzuordnung erst in Stufe 4
    assert first["evidence"]
