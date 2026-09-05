"""Preisbild einer Position ueber mehrere Angebote.

Ein einzelner Preis ist ein Datenpunkt, kein Marktpreis. Diese Auswertung sagt
deshalb nicht nur, was etwas kostet, sondern auch, wie belastbar die Aussage
ist: aus wie vielen Angeboten sie stammt und wie weit diese auseinanderliegen.

Eine grosse Streuung ist das nuetzlichste Warnsignal der ganzen Stufe - sie
bedeutet fast immer, dass unter den Angeboten etwas ist, das nicht dazugehoert.
"""

from __future__ import annotations

from statistics import median

from ..models.price import PriceStatistics, ProductMatch


def price_statistics(
    matches: list[ProductMatch],
    *,
    minimum_confidence: int,
    quantity: float | None = None,
    currencies: list[str] | None = None,
) -> tuple[PriceStatistics, list[str]]:
    """(Preisbild, Hinweise) aus den zugeordneten Angeboten.

    Gerechnet wird nur mit Nettopreisen: ein Bruttopreis ohne Steuersatz und
    ein Preis ohne Bezugsgroesse gehen nicht in die Statistik ein - sonst
    stuenden hier Zahlen nebeneinander, die verschiedene Dinge bedeuten.

    Waehrungen werden **nicht** umgerechnet. Ein aus der Luft gegriffener Kurs
    waere ein aus der Luft gegriffener Preis; stattdessen entscheidet die
    haeufigste Waehrung, und der Rest wird als Hinweis ausgewiesen.
    """
    warnings: list[str] = []
    statistics = PriceStatistics(offer_count=len(matches))
    if not matches:
        return statistics, warnings

    usable: list[tuple[str, float]] = []
    for match in matches:
        if match.match_confidence < minimum_confidence:
            continue
        net, reason = match.quote.net_amount(quantity)
        if net is None:
            if reason:
                warnings.append(f"{match.quote.supplier}: {reason}")
            continue
        currency = match.quote.currency
        if currency is None:
            warnings.append(f"{match.quote.supplier}: Waehrung nicht ausgewiesen")
            continue
        if currencies and currency.upper() not in {c.upper() for c in currencies}:
            warnings.append(
                f"{match.quote.supplier}: Waehrung {currency} nicht zugelassen - nicht umgerechnet"
            )
            continue
        usable.append((currency.upper(), net))

    if not usable:
        if statistics.offer_count:
            warnings.append(
                "Kein Angebot ist kalkulationsfaehig - Zuordnungsguete zu niedrig "
                "oder Preisangaben unvollstaendig."
            )
        return statistics, warnings

    # Mehrere Waehrungen: die haeufigste gewinnt, der Rest bleibt draussen.
    currency_counts: dict[str, int] = {}
    for currency, _amount in usable:
        currency_counts[currency] = currency_counts.get(currency, 0) + 1
    leading = max(currency_counts, key=lambda key: currency_counts[key])
    if len(currency_counts) > 1:
        warnings.append(
            f"Angebote in {len(currency_counts)} Waehrungen - gerechnet wird nur "
            f"mit {leading}, ohne Umrechnung."
        )
    amounts = sorted(amount for currency, amount in usable if currency == leading)

    statistics.usable_count = len(amounts)
    statistics.currency = leading
    statistics.minimum = amounts[0]
    statistics.maximum = amounts[-1]
    statistics.median = float(median(amounts))
    if statistics.median:
        statistics.spread_ratio = (statistics.maximum - statistics.minimum) / statistics.median
    return statistics, warnings
