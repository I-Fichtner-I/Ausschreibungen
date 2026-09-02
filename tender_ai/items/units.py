"""Mengeneinheiten normieren.

Leistungsverzeichnisse schreiben dieselbe Einheit auf ein Dutzend Arten:
"Stk", "Stck.", "Stueck", "St". Fuer die Preisrecherche muss daraus eine
vergleichbare Einheit werden - der Originaltext bleibt trotzdem erhalten,
damit die Position im Dokument wiederzufinden ist.

Eine unbekannte Einheit wird **nicht** geraten: sie liefert ``None`` und
erscheint spaeter als UNKNOWN.
"""

from __future__ import annotations

import re

#: Kanonische Einheit -> Schreibweisen im Dokument (bereits normalisiert:
#: kleingeschrieben, ohne Punkte, Umlaute transkribiert).
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "STK": ("stk", "stck", "stueck", "st", "psc", "pcs", "pc", "piece", "ea", "each"),
    "M": ("m", "lfm", "ldm", "lfdm", "laufmeter", "laufender meter", "meter", "mtr"),
    "M2": ("m2", "qm", "quadratmeter", "sqm"),
    "M3": ("m3", "cbm", "kubikmeter", "rm", "raummeter"),
    "KG": ("kg", "kilogramm", "kilo"),
    "T": ("t", "to", "tonne", "tonnen"),
    "G": ("g", "gr", "gramm"),
    "L": ("l", "ltr", "lt", "liter"),
    "ML": ("ml", "milliliter"),
    "H": ("h", "std", "stunde", "stunden", "akh", "hour", "hours"),
    "D": ("tag", "tage", "kalendertag", "kalendertage"),
    "WK": ("woche", "wochen"),
    "MON": ("monat", "monate", "mon"),
    "A": ("jahr", "jahre", "ja"),
    "PSCH": ("psch", "pausch", "pauschal", "pau", "pschl", "ls", "lump sum", "pauschale"),
    "PA": ("paar", "par", "pair"),
    "PCK": ("pack", "packung", "pkg", "pck", "vpe", "beutel"),
    "KRT": ("karton", "ktn", "krt", "kt", "box"),
    "SET": ("set", "satz", "garnitur", "grt", "sat"),
    "ROL": ("rolle", "rollen", "rl", "rol"),
    "PAL": ("palette", "paletten", "pal"),
    "BD": ("bund", "bd", "bd."),
    "PCT": ("prozent", "%", "v h"),
}

#: Umgekehrte Zuordnung, einmal aufgebaut.
_LOOKUP: dict[str, str] = {
    alias: canonical for canonical, aliases in UNIT_ALIASES.items() for alias in aliases
}

#: Hochgestellte Ziffern ausschreiben ("m²" -> "m2").
_SUPERSCRIPT = {"\u00b2": "2", "\u00b3": "3"}
_CLEAN = re.compile(r"[^\w%/ ]+", re.UNICODE)
_WS = re.compile(r"\s+")


def _normalize(raw: str) -> str:
    text = raw.strip().casefold()
    for symbol, replacement in _SUPERSCRIPT.items():
        text = text.replace(symbol, replacement)
    text = text.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = _CLEAN.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_unit(raw: str | None) -> tuple[str | None, str | None]:
    """(kanonische Einheit, Originaltext) - unbekannte Einheit bleibt ``None``.

    Der Originaltext wird immer zurueckgegeben, auch wenn die Einheit nicht
    zugeordnet werden konnte: die Angabe im Dokument geht nie verloren.
    """
    if raw is None:
        return None, None
    original = raw.strip()
    if not original:
        return None, None
    key = _normalize(original)
    if not key:
        return None, original
    canonical = _LOOKUP.get(key)
    if canonical:
        return canonical, original
    # "100 Stk" in der Einheitenspalte: die Einheit steckt im letzten Wort.
    parts = key.split()
    if len(parts) > 1:
        canonical = _LOOKUP.get(parts[-1])
        if canonical:
            return canonical, original
    return None, original


#: Einheiten sind kurz; alles Laengere ist eher Text als Mengeneinheit.
MAX_UNIT_LENGTH = 16


def looks_like_unit(raw: str | None) -> bool:
    """Heuristik fuer die Spaltenerkennung ohne Kopfzeile."""
    if not raw or len(raw.strip()) > MAX_UNIT_LENGTH:
        return False
    canonical, _original = normalize_unit(raw)
    return canonical is not None
