"""Aus Einkaufspreisen eine Kalkulation machen (Stufe 5).

Gerechnet wird ausschliesslich mit **Nettopreisen aus belastbar zugeordneten
Angeboten**. Eine Position ohne solchen Preis geht nicht mit null in die
Rechnung ein - sie bleibt unkalkuliert und senkt die Abdeckung. Genau das ist
der Unterschied zwischen "kostet nichts" und "wissen wir nicht".

Der Angebotspreis entsteht aus den Selbstkosten plus Aufschlag. Er ist eine
Rechnung, keine Abgabe: verbindlich wird nichts ohne Freigabe des Nutzers.
"""

from __future__ import annotations

from ..config import CalculationConfig
from ..models.calculation import PositionCost, Scenario, ScenarioKind
from ..models.price import ItemPricing


def _margin(cost: float, sale: float) -> tuple[float, float | None]:
    """(absolute Marge, Marge in Prozent des Angebotspreises)."""
    absolute = sale - cost
    percent = (absolute / sale * 100.0) if sale else None
    return absolute, percent


def position_cost(
    pricing: ItemPricing,
    *,
    config: CalculationConfig,
    minimum_confidence: int,
    unit_price: float | None = None,
) -> PositionCost:
    """Kosten und Erloes einer Position.

    ``unit_price`` erlaubt es, dieselbe Position mit einem anderen
    Einkaufspreis zu rechnen (Szenarien) - ohne die Zuordnung erneut zu
    bewerten.
    """
    cost = PositionCost(
        position=pricing.position,
        title=pricing.title,
        quantity=pricing.quantity,
        unit=pricing.unit,
    )
    best = pricing.best_match
    if best is not None:
        cost.supplier = best.quote.supplier
        cost.match_confidence = best.match_confidence

    if pricing.quantity is None:
        cost.warnings.append("Ohne Menge ist keine Kalkulation moeglich.")
        return cost
    if pricing.statistics.usable_count == 0 or best is None:
        cost.warnings.append(
            "Kein belastbar zugeordnetes Angebot - Position geht nicht in die Rechnung ein."
        )
        return cost
    if best.match_confidence < minimum_confidence:
        cost.warnings.append(
            f"Zuordnungsguete {best.match_confidence} unter der Schwelle "
            f"{minimum_confidence} - Position bleibt unkalkuliert."
        )
        return cost

    price = unit_price
    if price is None:
        price, reason = best.quote.net_amount(pricing.quantity)
        if price is None:
            cost.warnings.append(f"{reason} - Position bleibt unkalkuliert.")
            return cost

    cost.unit_purchase_price = price
    cost.purchase_total = price * pricing.quantity
    if config.include_shipping and best.quote.shipping_cost:
        cost.shipping_total = best.quote.shipping_cost
    cost.surcharge_total = (
        cost.purchase_total * config.overhead_percent / 100.0 + config.handling_cost_per_position
    )
    cost.cost_total = cost.purchase_total + cost.shipping_total + cost.surcharge_total
    cost.sale_total = cost.cost_total * (1.0 + config.markup_percent / 100.0)
    cost.margin_absolute, cost.margin_percent = _margin(cost.cost_total, cost.sale_total)
    return cost


#: Welcher Kennwert der Preisstatistik welches Szenario traegt.
SCENARIO_SOURCES: dict[ScenarioKind, str] = {
    ScenarioKind.BEST: "minimum",
    ScenarioKind.EXPECTED: "median",
    ScenarioKind.WORST: "maximum",
}


def build_scenario(
    kind: ScenarioKind,
    items: list[ItemPricing],
    *,
    config: CalculationConfig,
    minimum_confidence: int,
    currency: str | None,
    fixed_sale_total: float | None = None,
) -> Scenario:
    """Ein Szenario ueber alle kalkulierbaren Positionen.

    Die drei Einkaufspreise stammen aus echten Angeboten (guenstigstes,
    mittleres, teuerstes) - nicht aus einem Auf- und Abschlag auf eine einzige
    Zahl. Ein erfundener Streubereich waere eine erfundene Sicherheit.

    ``fixed_sale_total`` haelt den Angebotspreis fest. Das ist der Kern der
    Szenarien: in einer Ausschreibung wird **ein** Preis geboten, eingekauft
    wird spaeter. Waechst der Angebotspreis mit dem Einkauf mit, ist die Marge
    in jedem Fall gleich und die Tabelle sagt nichts. Erst ein fester
    Angebotspreis zeigt, was ein teurerer Einkauf tatsaechlich kostet.
    """
    scenario = Scenario(kind=kind, currency=currency)
    attribute = SCENARIO_SOURCES[kind]
    own_sale_total = 0.0
    for item in items:
        price = getattr(item.statistics, attribute)
        cost = position_cost(
            item,
            config=config,
            minimum_confidence=minimum_confidence,
            unit_price=price,
        )
        if cost.cost_total is None or cost.sale_total is None:
            continue
        scenario.cost_total += cost.cost_total
        own_sale_total += cost.sale_total

    scenario.cost_total += config.fixed_cost_per_tender
    own_sale_total += config.fixed_cost_per_tender * (1.0 + config.markup_percent / 100.0)
    scenario.sale_total = own_sale_total if fixed_sale_total is None else fixed_sale_total
    scenario.margin_absolute, scenario.margin_percent = _margin(
        scenario.cost_total, scenario.sale_total
    )
    if scenario.cost_total:
        scenario.roi_percent = scenario.margin_absolute / scenario.cost_total * 100.0
    return scenario
