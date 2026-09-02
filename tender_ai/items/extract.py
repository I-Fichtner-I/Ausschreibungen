"""Positionen aus den ausgelesenen Vergabeunterlagen gewinnen (Stufe 3).

Der Weg fuehrt ueber die in Stufe 2 erkannten Tabellen: ein
Leistungsverzeichnis ist fast immer eine Tabelle, und eine Tabellenzeile
liefert Menge und Einheit getrennt statt in einem Fliesstext verpackt.
Nur wenn keine brauchbare Tabelle gefunden wird, greift die Rueckfallebene
ueber Positionsmuster im Text - erkennbar an ``source_kind = TEXT`` und mit
niedrigerer Konfidenz, damit niemand beide Ergebnisse gleich behandelt.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from ..analysis.requirements import EQUIVALENCE_PATTERN
from ..models.common import Provenance, normalize_text
from ..models.document import ExtractedDocument, ExtractedTable
from ..models.item import MAX_CONFIDENCE, ItemExtractionResult, ItemSourceKind, TenderItem
from ..sources.parsing import parse_amount_with_confidence
from .columns import POSITION_PATTERN, ColumnRole, infer_columns, is_item_table, map_columns
from .units import normalize_unit

#: Zeilen, die keine Position sind, sondern eine Zwischenrechnung.
SUMMARY_PATTERN = re.compile(
    r"^\s*(?:zwischen|gesamt|end)?summe\b|^\s*uebertrag\b|^\s*ueber\s*trag\b"
    r"|^\s*gesamtbetrag\b|^\s*nettosumme\b|^\s*mehrwertsteuer\b|^\s*mwst\b"
    r"|^\s*umsatzsteuer\b|^\s*titel\b|^\s*los\s+\d|^\s*hinweis\b",
    re.IGNORECASE,
)
#: "ca. 20", "rund 100" - die Menge ist ausdruecklich ungefaehr.
APPROXIMATE_PATTERN = re.compile(
    r"\b(?:ca|circa|rund|etwa|ungefaehr|ungef\u00e4hr|ab)\b\.?", re.IGNORECASE
)
#: Menge steht nicht fest ("auf Abruf", "nach Bedarf").
_ON_DEMAND = re.compile(
    r"abruf|nach\s+bedarf|n\.\s*bed|bedarfsabhaengig|bedarfsabh\u00e4ngig", re.IGNORECASE
)

#: Attribute, die im Positionstext ausgeschrieben stehen.
_MANUFACTURER_PATTERN = re.compile(
    r"\b(?:Fabrikat|Hersteller|Marke)\s*[:=]\s*([^,;\n|]{2,60})", re.IGNORECASE
)
_MODEL_PATTERN = re.compile(
    r"\b(?:Typ|Modell|Ausf(?:ue|ü)hrung|Typenbezeichnung)\s*[:=]\s*([^,;\n|]{1,60})",
    re.IGNORECASE,
)
_ARTICLE_PATTERN = re.compile(
    r"\b(?:Art(?:ikel)?\.?\s*-?\s*Nr|Artikelnummer|Bestellnummer|Best\.?-?Nr|EAN|GTIN)\s*"
    r"[.:=]?\s*([A-Za-z0-9][A-Za-z0-9./_-]{2,32})",
    re.IGNORECASE,
)
#: Merkmale, die fuer die Preisrecherche zaehlen. Bewusst eine feste Liste -
#: ein generisches "Wort: Wert" wuerde halbe Saetze als Merkmal ausgeben.
SPEC_KEYS = (
    "Farbe",
    "Material",
    "Abmessung",
    "Abmessungen",
    "Masse",
    "Maße",
    "Groesse",
    "Größe",
    "Gewicht",
    "Leistung",
    "Spannung",
    "Norm",
    "Klasse",
    "Breite",
    "Hoehe",
    "Höhe",
    "Tiefe",
    "Laenge",
    "Länge",
    "Durchmesser",
    "Kapazitaet",
    "Kapazität",
    "Aufloesung",
    "Auflösung",
    "Anschluss",
)
_SPEC_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(key) for key in SPEC_KEYS) + r")\s*[:=]\s*([^,;\n|]{1,40})",
    re.IGNORECASE,
)

#: Positionszeile im Fliesstext: "1.10  20 Stk Buerostuhl, drehbar".
_TEXT_POSITION = re.compile(
    r"^\s*(?:Pos\.?\s*)?(?P<position>\d{1,4}(?:[.]\d{1,4}){0,3})[.):]?\s+(?P<rest>\S.{3,300})$",
    re.IGNORECASE,
)
#: Menge mit Einheit irgendwo in der Zeile. Nach der Einheit darf ein
#: Satzzeichen folgen ("15 Stueck, hoehenverstellbar") - deshalb wird ueber
#: alle Treffer iteriert und der erste mit bekannter Einheit genommen.
_TEXT_QUANTITY = re.compile(
    r"(?P<quantity>\d[\d.,]*)\s*(?P<unit>[A-Za-zÄÖÜäöü²³%]{1,12})\.?(?=[\s,;.)]|$)"
)

#: Mindestzeilen, wenn die Spaltenbelegung nur geraten wurde (keine Kopfzeile).
#: Mit ausgeschriebener LV-Kopfzeile genuegt eine einzige Position.
MIN_TABLE_ROWS = 2
#: Schutz gegen entartete Dokumente (Preisblatt mit tausenden Zeilen).
MAX_ITEMS = 2000
#: Beschreibungen unter dieser Laenge sind kein Artikel, sondern ein Kuerzel.
MIN_TITLE_LENGTH = 3
#: Erste Zeile der Beschreibung als Titel; der Rest wird Beschreibungstext.
MAX_TITLE_LENGTH = 120


def _cell(row: Sequence[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    value = row[index]
    return value.strip() if isinstance(value, str) else str(value).strip()


def _parse_quantity(raw: str) -> tuple[float | None, bool, str | None]:
    """(Menge, geschaetzt, Warnung) - unlesbare Mengen werden nie geraten."""
    if not raw:
        return None, False, None
    if _ON_DEMAND.search(raw):
        return None, False, f"Menge steht nicht fest: {raw!r}"
    approximate = bool(APPROXIMATE_PATTERN.search(raw))
    cleaned = APPROXIMATE_PATTERN.sub(" ", raw).strip()
    value, confidence = parse_amount_with_confidence(cleaned)
    if value is None:
        return None, False, f"Menge nicht lesbar: {raw!r}"
    if value < 0:
        return None, False, f"Negative Menge uebersprungen: {raw!r}"
    # Konfidenz < 100 heisst: Trennzeichen mehrdeutig ("1.234"). Der Wert wird
    # uebernommen, aber als Schaetzung gekennzeichnet statt still verwendet.
    ambiguous = confidence < 100
    warning = f"Mengentrennzeichen mehrdeutig: {raw!r}" if ambiguous else None
    return value, approximate or ambiguous, warning


def _split_title(text: str) -> tuple[str, str | None]:
    """Erste Zeile als Titel, vollstaendigen Text als Beschreibung behalten."""
    collapsed = re.sub(r"\s*\n\s*", " ¶ ", text.strip())
    first = collapsed.split(" ¶ ", 1)[0].strip()
    if len(first) > MAX_TITLE_LENGTH:
        cut = first[:MAX_TITLE_LENGTH].rsplit(" ", 1)[0]
        first = f"{cut}..." if cut else first[:MAX_TITLE_LENGTH]
    full = collapsed.replace(" ¶ ", " ").strip()
    description = full if full != first else None
    return first, description


def _clean_attribute(value: str | None) -> str | None:
    """Zusaetze wie "oder gleichwertig" gehoeren nicht in den Typnamen.

    Sie bleiben als Klausel erhalten (``brand_locked``), wuerden als Teil der
    Typbezeichnung aber jede Produktsuche in Stufe 4 ins Leere laufen lassen.
    """
    if value is None:
        return None
    cleaned = EQUIVALENCE_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    return cleaned or None


def _attributes(text: str) -> tuple[str | None, str | None, str | None, dict[str, str]]:
    """Hersteller, Typ, Artikelnummer und Merkmale aus dem Positionstext."""
    manufacturer_match = _MANUFACTURER_PATTERN.search(text)
    model_match = _MODEL_PATTERN.search(text)
    article_match = _ARTICLE_PATTERN.search(text)
    specifications: dict[str, str] = {}
    for key, value in _SPEC_PATTERN.findall(text):
        specifications.setdefault(key.strip().title(), value.strip())
    return (
        _clean_attribute(manufacturer_match.group(1)) if manufacturer_match else None,
        _clean_attribute(model_match.group(1)) if model_match else None,
        article_match.group(1).strip() if article_match else None,
        specifications,
    )


def _score(item: TenderItem, *, base: int) -> int:
    """Erkennungs-Konfidenz: wie vollstaendig wurde die Zeile gelesen?"""
    score = base
    if item.quantity is not None:
        score += 12 if item.quantity_estimated else 20
    if item.unit:
        score += 10
    elif item.unit_original:
        score += 3  # Einheit steht da, ist aber unbekannt
    if item.position:
        score += 8
    if item.manufacturer or item.model_number:
        score += 8
    if item.article_number:
        score += 5
    if item.specifications:
        score += 4
    if len(item.title) >= 12:
        score += 5
    return max(0, min(MAX_CONFIDENCE, score))


def _finalize(item: TenderItem, *, base: int, evidence: str) -> TenderItem:
    # Jeden Textteil einzeln pruefen: aneinandergehaengt wuerde ein Merkmal am
    # Ende eines Feldes den Anfang des naechsten mitlesen ("Farbe: schwarz"
    # plus folgende Spalte).
    for text in (item.title, item.description, evidence):
        if not text:
            continue
        manufacturer, model, article, specifications = _attributes(text)
        item.manufacturer = item.manufacturer or manufacturer
        item.model_number = item.model_number or model
        item.article_number = item.article_number or article
        for key, value in specifications.items():
            item.specifications.setdefault(key, value)
    # Fabrikatsvorgabe ohne Gleichwertigkeitsklausel in derselben Position:
    # genau die Konstellation, die Alternativangebote ausschliesst.
    if item.manufacturer or item.model_number:
        item.brand_locked = not EQUIVALENCE_PATTERN.search(evidence)
    item.confidence = _score(item, base=base)
    return item


def items_from_table(table: ExtractedTable, document: ExtractedDocument) -> list[TenderItem]:
    """Zeilen einer Tabelle als Positionen lesen; leere Liste wenn kein LV."""
    mapping = map_columns(table.header)
    from_header = is_item_table(mapping)
    if not from_header:
        # Ohne (brauchbare) Kopfzeile entscheidet der Inhalt der Spalten.
        mapping = infer_columns(table.rows)
        if not is_item_table(mapping):
            return []
        # Eine geratene Spaltenbelegung braucht mehr Evidenz als eine
        # ausgeschriebene Kopfzeile: ein einzelner Formularkasten mit drei
        # Zellen soll nicht als Leistungsverzeichnis durchgehen.
        if len(table.rows) < MIN_TABLE_ROWS:
            return []

    description_index = mapping[ColumnRole.DESCRIPTION]
    items: list[TenderItem] = []
    for row in table.rows:
        description_cell = _cell(row, description_index)
        if len(description_cell) < MIN_TITLE_LENGTH or SUMMARY_PATTERN.match(description_cell):
            continue
        title, description = _split_title(description_cell)
        quantity, estimated, warning = _parse_quantity(_cell(row, mapping.get(ColumnRole.QUANTITY)))
        unit, unit_original = normalize_unit(_cell(row, mapping.get(ColumnRole.UNIT)) or None)
        position = _cell(row, mapping.get(ColumnRole.POSITION)) or None
        if position and not POSITION_PATTERN.match(position):
            position = None

        evidence = " | ".join(
            cell.strip() for cell in row if isinstance(cell, str) and cell.strip()
        )
        item = TenderItem(
            position=position,
            title=title,
            description=description,
            quantity=quantity,
            quantity_estimated=estimated,
            unit=unit,
            unit_original=unit_original,
            manufacturer=_cell(row, mapping.get(ColumnRole.MANUFACTURER)) or None,
            model_number=_cell(row, mapping.get(ColumnRole.MODEL)) or None,
            article_number=_cell(row, mapping.get(ColumnRole.ARTICLE_NUMBER)) or None,
            source_kind=ItemSourceKind.TABLE,
            warnings=[warning] if warning else [],
            provenance=Provenance(
                source="document",
                method="document",
                document=document.file_name or document.source_path,
                page=table.page,
                section=table.section,
                original_text=evidence[:1000],
            ),
        )
        if unit_original and not unit:
            item.warnings.append(f"Einheit unbekannt: {unit_original!r}")
        items.append(_finalize(item, base=40, evidence=evidence))
    return items


def items_from_text(document: ExtractedDocument) -> list[TenderItem]:
    """Rueckfallebene: Positionsmuster im Fliesstext.

    Bewusst zurueckhaltend - erkannt wird nur, was Ordnungszahl **und** Menge
    mit Einheit traegt. Alles andere waere Raten.
    """
    items: list[TenderItem] = []
    for page in document.pages:
        for line in page.text.splitlines():
            match = _TEXT_POSITION.match(line)
            if not match:
                continue
            rest = match.group("rest").strip()
            if SUMMARY_PATTERN.match(rest):
                continue
            quantity_match = None
            unit = unit_original = None
            for candidate in _TEXT_QUANTITY.finditer(rest):
                unit, unit_original = normalize_unit(candidate.group("unit"))
                if unit is not None:
                    quantity_match = candidate
                    break
            if quantity_match is None:
                continue  # ohne erkannte Einheit ist es keine Mengenangabe
            quantity, estimated, warning = _parse_quantity(quantity_match.group("quantity"))
            if quantity is None:
                continue
            remainder = rest[: quantity_match.start()] + " " + rest[quantity_match.end() :]
            # Die herausgeschnittene Mengenangabe laesst Satzzeichen zurueck
            # ("Schreibtisch, , hoehenverstellbar").
            remainder = re.sub(r"\s+", " ", remainder)
            remainder = re.sub(r"(?:\s*[,;]\s*){2,}", ", ", remainder)
            remainder = remainder.strip(" -:;,")
            if len(remainder) < MIN_TITLE_LENGTH:
                continue
            title, description = _split_title(remainder)
            item = TenderItem(
                position=match.group("position"),
                title=title,
                description=description,
                quantity=quantity,
                quantity_estimated=estimated,
                unit=unit,
                unit_original=unit_original,
                source_kind=ItemSourceKind.TEXT,
                warnings=[warning] if warning else [],
                provenance=Provenance(
                    source="document",
                    method="document",
                    document=document.file_name or document.source_path,
                    page=page.number,
                    original_text=line.strip()[:1000],
                ),
            )
            # Niedrigere Basis als bei Tabellen: eine Textzeile kann auch ein
            # Satz sein, der zufaellig wie eine Position aussieht.
            items.append(_finalize(item, base=25, evidence=line))
    return items


def deduplicate(items: Iterable[TenderItem]) -> list[TenderItem]:
    """Dieselbe Position aus mehreren Dateien auf einen Eintrag zusammenfuehren.

    Dasselbe Leistungsverzeichnis liegt oft als PDF *und* als Preisblatt bei.
    Bei gleichem Schluessel gewinnt der vollstaendiger gelesene Eintrag; die
    Fundstelle des unterlegenen wird als Hinweis vermerkt, damit die zweite
    Quelle nicht spurlos verschwindet.
    """
    best: dict[tuple[str, str, str], TenderItem] = {}
    order: list[tuple[str, str, str]] = []
    for item in items:
        key = item.dedup_key()
        if not key[1]:
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = item
            order.append(key)
            continue
        winner, loser = (
            (item, existing) if item.confidence > existing.confidence else (existing, item)
        )
        source = loser.provenance.document if loser.provenance else None
        if source and source != (winner.provenance.document if winner.provenance else None):
            note = f"Auch enthalten in: {source}"
            if note not in winner.warnings:
                winner.warnings.append(note)
        best[key] = winner
    return [best[key] for key in order]


def _sort_key(item: TenderItem) -> tuple[int, tuple[int, ...], str]:
    """Nach Ordnungszahl sortieren - "1.10" gehoert hinter "1.9"."""
    if not item.position:
        return (1, (), normalize_text(item.title))
    parts = tuple(
        int(part) for part in re.split(r"[./-]", item.position.rstrip(".")) if part.isdigit()
    )
    return (0, parts, normalize_text(item.title))


def extract_items(
    documents: Sequence[ExtractedDocument],
    *,
    tender_id: str,
    max_items: int = MAX_ITEMS,
) -> ItemExtractionResult:
    """Alle Positionen einer Ausschreibung erkennen.

    Ein defektes Dokument stoppt die Erkennung nicht: es wird uebersprungen und
    im Ergebnis als Hinweis vermerkt.
    """
    result = ItemExtractionResult(tender_id=tender_id, documents_scanned=len(documents))
    collected: list[TenderItem] = []
    for document in documents:
        result.tables_scanned += len(document.tables)
        for table in document.tables:
            found = items_from_table(table, document)
            if found:
                result.tables_used += 1
                collected.extend(found)

    if not collected:
        for document in documents:
            collected.extend(items_from_text(document))
        if collected:
            result.warnings.append(
                "Keine auswertbare Tabelle gefunden - Positionen stammen aus dem Fliesstext "
                "und sind entsprechend unsicher."
            )

    items = deduplicate(collected)
    if len(items) > max_items:
        result.warnings.append(
            f"Mehr als {max_items} Positionen erkannt - Ergebnis wurde gekuerzt."
        )
        items = items[:max_items]
    result.items = sorted(items, key=_sort_key)

    if not result.items:
        if result.documents_scanned == 0:
            result.warnings.append("Keine ausgelesenen Unterlagen vorhanden.")
        else:
            result.warnings.append(
                "Keine Positionen erkannt - moeglicherweise liegt das Leistungsverzeichnis "
                "nur als Scan oder in einem geschuetzten Format vor."
            )
    elif not result.total_quantity_known:
        missing = sum(1 for item in result.items if item.quantity is None)
        result.warnings.append(f"{missing} Position(en) ohne erkannte Menge.")
    return result
