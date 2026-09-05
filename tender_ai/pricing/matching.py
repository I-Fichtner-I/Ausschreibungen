"""Positionen des Leistungsverzeichnisses konkreten Produkten zuordnen.

Das ist die Stelle, an der eine Kalkulation still falsch werden kann: ein
aehnlich klingendes Produkt zum halben Preis laesst jede Marge grossartig
aussehen, bis geliefert werden muss. Deshalb ist die Zuordnung **additiv und
begruendet** - jede Guete entsteht aus benannten Gruenden, und was dagegen
spricht, steht als eigener Punkt daneben statt im Score zu verschwinden.

Die Rangfolge folgt der Belastbarkeit des Merkmals:

* Artikelnummer identisch - das staerkste Merkmal, aber kein Beweis
* Hersteller **und** Typ - stark, solange beide aus dem Dokument stammen
* nur Typ oder nur Hersteller - ein Hinweis
* Wortueberdeckung im Namen - schwach, allein nie kalkulationsfaehig
"""

from __future__ import annotations

import re

from ..models.common import normalize_text
from ..models.item import TenderItem
from ..models.price import MAX_MATCH_CONFIDENCE, PriceQuote, ProductMatch

#: Punkte je Merkmal. Bewusst so geschnitten, dass Wortueberdeckung allein die
#: uebliche Schwelle (85) nicht erreicht: ein aehnlicher Name ist kein Produkt.
POINTS_ARTICLE_NUMBER = 95
POINTS_MANUFACTURER_AND_MODEL = 85
POINTS_MODEL_ONLY = 65
POINTS_MANUFACTURER_ONLY = 35
#: Hoechstwert, den blosse Namensaehnlichkeit erreichen kann.
MAX_POINTS_TITLE_ONLY = 60
#: Identische Bezeichnung ohne Hersteller/Typ. Deutlich staerker als eine
#: Teilueberdeckung, aber bewusst unter der ueblichen Schwelle (85): denselben
#: "Buerostuhl drehbar mit Armlehne" verkaufen mehrere Hersteller.
POINTS_TITLE_IDENTICAL = 75
#: Ab dieser Wortueberdeckung gilt ein Name ueberhaupt als aehnlich.
MIN_TITLE_OVERLAP = 0.34
#: Obergrenze, wenn die Position eine Fabrikatsvorgabe traegt und das Angebot
#: ein anderes Fabrikat nennt. Ein Alternativprodukt ist dort kein Treffer.
BRAND_MISMATCH_CAP = 20
#: Obergrenze bei abweichender Mengeneinheit - Preis je Meter und Preis je
#: Stueck sind nicht vergleichbar.
UNIT_MISMATCH_CAP = 55

#: Woerter, die in fast jeder Bezeichnung stehen und nichts unterscheiden.
STOPWORDS = frozenset(
    (
        "und",
        "oder",
        "mit",
        "ohne",
        "fuer",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "eines",
        "inkl",
        "inklusive",
        "zzgl",
        "je",
        "pro",
        "stk",
        "stueck",
        "lieferung",
        "montage",
        "neu",
    )
)
_TOKEN = re.compile(r"[a-z0-9]+")


#: Deutsche Flexionsendungen, die fuer den Abgleich wegfallen duerfen. Ohne
#: sie haben "Lieferung von Monitoren" und "Monitor 27 Zoll" null gemeinsame
#: Woerter - der haeufigste Fall ueberhaupt. Bewusst kein vollstaendiger
#: Stemmer: "er" bleibt drin, sonst wird aus "Messer" ein "Mess".
_ENDINGS = ("en", "es", "er", "e", "n", "s")
#: Kuerzer darf ein Wortstamm nach dem Kuerzen nicht werden.
MIN_STEM_LENGTH = 4


def _stem(token: str) -> str:
    if len(token) <= MIN_STEM_LENGTH:
        return token
    for ending in _ENDINGS:
        if ending == "er":
            continue  # zu riskant: "Messer" -> "Mess"
        if token.endswith(ending) and len(token) - len(ending) >= MIN_STEM_LENGTH:
            return token[: -len(ending)]
    return token


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        _stem(token)
        for token in _TOKEN.findall(normalize_text(text))
        if len(token) > 1 and token not in STOPWORDS
    }


def normalize_identifier(value: str | None) -> str:
    """Artikel-/Typnummern vergleichbar machen ("MX-27" == "mx 27")."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def title_overlap(left: str | None, right: str | None) -> float:
    """Anteil gemeinsamer Woerter, bezogen auf die kuerzere Bezeichnung.

    Bewusst nicht Jaccard: eine ausfuehrliche Katalogbeschreibung soll nicht
    dafuer bestraft werden, dass sie mehr Woerter enthaelt als die Position.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / min(len(left_tokens), len(right_tokens))


def match_quote(item: TenderItem, quote: PriceQuote) -> ProductMatch:
    """Ein Angebot gegen eine Position bewerten - mit Begruendung."""
    reasons: list[str] = []
    concerns: list[str] = []
    score = 0

    item_article = normalize_identifier(item.article_number)
    quote_article = normalize_identifier(quote.article_number)
    item_model = normalize_identifier(item.model_number)
    quote_model = normalize_identifier(quote.model_number)
    manufacturer_match = (
        bool(item.manufacturer)
        and bool(quote.manufacturer)
        and normalize_text(item.manufacturer) == normalize_text(quote.manufacturer)
    )
    model_match = bool(item_model) and item_model == quote_model
    overlap = title_overlap(item.title, quote.product_name)

    if item_article and item_article == quote_article:
        score = POINTS_ARTICLE_NUMBER
        reasons.append(f"Artikelnummer identisch ({item.article_number})")
    elif manufacturer_match and model_match:
        score = POINTS_MANUFACTURER_AND_MODEL
        reasons.append(f"Hersteller und Typ identisch ({item.manufacturer} {item.model_number})")
    elif model_match:
        score = POINTS_MODEL_ONLY
        reasons.append(f"Typ identisch ({item.model_number}), Hersteller nicht bestaetigt")
    elif manufacturer_match:
        score = POINTS_MANUFACTURER_ONLY
        reasons.append(f"Hersteller identisch ({item.manufacturer}), Typ nicht bestaetigt")

    names_identical = bool(item.title) and normalize_text(item.title) == normalize_text(
        quote.product_name
    )
    if overlap >= MIN_TITLE_OVERLAP:
        percent = round(overlap * 100)
        if score == 0 and names_identical:
            # Nur der Name passt, dafuer exakt: ein starker Hinweis, aber ohne
            # Hersteller oder Typ keine Grundlage fuer eine Marge.
            score = POINTS_TITLE_IDENTICAL
            reasons.append("Bezeichnung identisch, Hersteller/Typ nicht angegeben")
        elif score == 0:
            # Nur der Name passt teilweise: ein Vorschlag, keine Grundlage.
            score = min(MAX_POINTS_TITLE_ONLY, round(overlap * MAX_POINTS_TITLE_ONLY))
            reasons.append(f"Bezeichnung stimmt zu {percent} Prozent ueberein")
        else:
            score = min(MAX_MATCH_CONFIDENCE, score + 5)
            reasons.append(f"Bezeichnung stuetzt die Zuordnung ({percent} Prozent)")
    elif score > 0:
        concerns.append("Bezeichnungen weichen deutlich voneinander ab")

    # --- Was gegen die Zuordnung spricht, deckelt sie ----------------------
    if item.brand_locked and item.manufacturer and quote.manufacturer and not manufacturer_match:
        concerns.append(
            f"Fabrikatsvorgabe {item.manufacturer!r} nicht erfuellt "
            f"(Angebot: {quote.manufacturer!r})"
        )
        score = min(score, BRAND_MISMATCH_CAP)
    if item.unit and quote.unit and item.unit != quote.unit:
        concerns.append(f"Einheit weicht ab ({item.unit} gefordert, {quote.unit} angeboten)")
        score = min(score, UNIT_MISMATCH_CAP)
    if not quote.has_price:
        concerns.append("Angebot enthaelt keinen verwertbaren Preis")
    else:
        # Ein Preis, aus dem sich kein Nettobetrag ableiten laesst, taugt nicht
        # zur Kalkulation - egal ob die Bezugsgroesse ganz fehlt oder nur der
        # Steuersatz zum Bruttopreis.
        _net, reason = quote.net_amount()
        if reason:
            concerns.append(f"{reason} - Preis ist nicht kalkulationsfaehig")

    if score == 0:
        reasons.append("Kein belastbares gemeinsames Merkmal")

    return ProductMatch(
        quote=quote,
        match_confidence=max(0, min(MAX_MATCH_CONFIDENCE, score)),
        reasons=reasons,
        concerns=concerns,
    )


def match_quotes(
    item: TenderItem, quotes: list[PriceQuote], *, limit: int = 10
) -> list[ProductMatch]:
    """Alle Angebote bewerten, beste zuerst; Angebote ohne Merkmal fallen raus."""
    matches = [match_quote(item, quote) for quote in quotes]
    matches = [match for match in matches if match.match_confidence > 0]
    matches.sort(
        key=lambda match: (match.match_confidence, -(match.quote.amount or float("inf"))),
        reverse=True,
    )
    return matches[:limit]
