"""Stufe 5: Kosten, Szenarien, Mindestkriterien und Urteil."""

from __future__ import annotations

import pytest

from tender_ai.calculation.costs import build_scenario, position_cost
from tender_ai.calculation.scoring import evaluate_criteria, review_notes, score_calculation
from tender_ai.config import CalculationConfig, CriteriaConfig, ScoringConfig
from tender_ai.models.calculation import ScenarioKind, TenderCalculation, Verdict
from tender_ai.models.price import (
    ItemPricing,
    PriceBasis,
    PriceQuote,
    PriceStatistics,
    ProductMatch,
)

CONFIG = CalculationConfig(
    markup_percent=25.0,
    overhead_percent=0.0,
    handling_cost_per_position=0.0,
    fixed_cost_per_tender=0.0,
    include_shipping=False,
)


def _pricing(
    *,
    quantity: float | None = 10.0,
    amounts: tuple[float, ...] = (100.0,),
    confidence: int = 90,
    basis: PriceBasis = PriceBasis.NET,
    shipping: float | None = None,
) -> ItemPricing:
    pricing = ItemPricing(position="1.10", title="Monitor", quantity=quantity, unit="STK")
    for index, amount in enumerate(amounts):
        pricing.matches.append(
            ProductMatch(
                quote=PriceQuote(
                    supplier=f"Lieferant {index}",
                    product_name="Monitor",
                    amount=amount,
                    currency="EUR",
                    basis=basis,
                    shipping_cost=shipping,
                ),
                match_confidence=confidence,
            )
        )
    if basis is PriceBasis.NET:
        ordered = sorted(amounts)
        pricing.statistics = PriceStatistics(
            offer_count=len(amounts),
            usable_count=len(amounts),
            currency="EUR",
            minimum=ordered[0],
            maximum=ordered[-1],
            median=ordered[len(ordered) // 2],
        )
    else:
        pricing.statistics = PriceStatistics(offer_count=len(amounts))
    return pricing


# --------------------------------------------------------------------------
# Kosten je Position
# --------------------------------------------------------------------------


def test_costs_and_margin_of_a_position():
    cost = position_cost(_pricing(), config=CONFIG, minimum_confidence=85)
    assert cost.purchase_total == 1000.0
    assert cost.cost_total == 1000.0
    assert cost.sale_total == 1250.0
    assert cost.margin_absolute == 250.0
    assert cost.margin_percent == pytest.approx(20.0)  # 250 von 1250


def test_overhead_and_handling_enter_the_cost():
    config = CalculationConfig(
        markup_percent=0.0,
        overhead_percent=10.0,
        handling_cost_per_position=25.0,
        include_shipping=False,
    )
    cost = position_cost(_pricing(), config=config, minimum_confidence=85)
    assert cost.surcharge_total == pytest.approx(125.0)  # 100 Gemeinkosten + 25 Handling
    assert cost.cost_total == pytest.approx(1125.0)


def test_shipping_counts_only_when_configured():
    with_shipping = position_cost(
        _pricing(shipping=49.0),
        config=CalculationConfig(markup_percent=0.0, overhead_percent=0.0, include_shipping=True),
        minimum_confidence=85,
    )
    assert with_shipping.shipping_total == 49.0
    assert with_shipping.cost_total == 1049.0

    without = position_cost(_pricing(shipping=49.0), config=CONFIG, minimum_confidence=85)
    assert without.shipping_total == 0.0


def test_position_without_quantity_is_not_calculated():
    """Ohne Menge gibt es keine Summe - und keine erfundene Eins."""
    cost = position_cost(_pricing(quantity=None), config=CONFIG, minimum_confidence=85)
    assert not cost.is_calculated
    assert cost.cost_total is None
    assert any("Ohne Menge" in warning for warning in cost.warnings)


def test_weak_match_is_not_calculated():
    cost = position_cost(_pricing(confidence=60), config=CONFIG, minimum_confidence=85)
    assert not cost.is_calculated
    assert any("Zuordnungsguete" in warning for warning in cost.warnings)


def test_position_without_usable_price_is_not_calculated_as_zero():
    """Der Unterschied zwischen "kostet nichts" und "wissen wir nicht"."""
    cost = position_cost(_pricing(basis=PriceBasis.UNKNOWN), config=CONFIG, minimum_confidence=85)
    assert not cost.is_calculated
    assert cost.cost_total is None  # nicht 0.0
    assert cost.warnings


# --------------------------------------------------------------------------
# Szenarien
# --------------------------------------------------------------------------


def test_scenarios_share_one_offer_price_so_the_margin_moves():
    """In einer Ausschreibung wird einmal geboten, eingekauft wird spaeter.

    Waechst der Angebotspreis mit dem Einkauf mit, ist die Marge in jedem Fall
    gleich - und die Szenarien sagen nichts.
    """
    items = [_pricing(amounts=(90.0, 100.0, 110.0))]
    expected = build_scenario(
        ScenarioKind.EXPECTED, items, config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    best = build_scenario(
        ScenarioKind.BEST,
        items,
        config=CONFIG,
        minimum_confidence=85,
        currency="EUR",
        fixed_sale_total=expected.sale_total,
    )
    worst = build_scenario(
        ScenarioKind.WORST,
        items,
        config=CONFIG,
        minimum_confidence=85,
        currency="EUR",
        fixed_sale_total=expected.sale_total,
    )

    assert best.sale_total == expected.sale_total == worst.sale_total
    assert best.cost_total < expected.cost_total < worst.cost_total
    assert best.margin_percent > expected.margin_percent > worst.margin_percent


def test_fixed_cost_per_tender_enters_every_scenario():
    config = CalculationConfig(
        markup_percent=0.0,
        overhead_percent=0.0,
        include_shipping=False,
        fixed_cost_per_tender=500.0,
    )
    scenario = build_scenario(
        ScenarioKind.EXPECTED, [_pricing()], config=config, minimum_confidence=85, currency="EUR"
    )
    assert scenario.cost_total == 1500.0


def test_uncalculable_positions_are_left_out_of_the_sum():
    items = [_pricing(), _pricing(quantity=None)]
    scenario = build_scenario(
        ScenarioKind.EXPECTED, items, config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    assert scenario.cost_total == 1000.0  # nur die kalkulierbare Position


# --------------------------------------------------------------------------
# Mindestkriterien
# --------------------------------------------------------------------------


def _calculation(**overrides) -> TenderCalculation:
    payload: dict = {"tender_id": "t-1", "coverage_percent": 100}
    payload.update(overrides)
    return TenderCalculation(**payload)


def test_criteria_are_checked_against_the_expected_case():
    calculation = _calculation(risk_score=20)
    scenario = build_scenario(
        ScenarioKind.EXPECTED, [_pricing()], config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    results = evaluate_criteria(
        calculation, scenario, criteria=CriteriaConfig(), days_until_deadline=30
    )
    by_code = {result.code: result for result in results}
    assert by_code["margin"].passed  # 20 % >= 15 %
    assert by_code["risk"].passed  # 20 <= 40
    assert by_code["deadline"].passed  # 30 >= 3 Tage
    # 250 EUR Deckungsbeitrag reichen nicht: das Kriterium faellt durch, und
    # zwar mit den Zahlen daneben statt als blosses "nein".
    assert not by_code["profit"].passed
    assert by_code["profit"].required == ">= 500"
    assert by_code["profit"].actual == "250"
    assert not by_code["profit"].undetermined


def test_missing_data_never_counts_as_passed():
    """Eine Datenluecke darf nicht aussehen wie ein erfuelltes Kriterium."""
    results = evaluate_criteria(
        _calculation(), None, criteria=CriteriaConfig(), days_until_deadline=None
    )
    assert all(not result.passed for result in results)
    assert all(result.undetermined for result in results)
    assert {result.actual for result in results} == {"UNKNOWN"}


def test_unanalysed_risk_is_open_not_passed():
    scenario = build_scenario(
        ScenarioKind.EXPECTED, [_pricing()], config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    results = evaluate_criteria(
        _calculation(risk_score=None), scenario, criteria=CriteriaConfig(), days_until_deadline=30
    )
    risk = next(result for result in results if result.code == "risk")
    assert risk.undetermined and not risk.passed


# --------------------------------------------------------------------------
# Urteil
# --------------------------------------------------------------------------


def _scored(coverage: int, margin: float, risk: int | None = 10) -> TenderCalculation:
    calculation = _calculation(coverage_percent=coverage, risk_score=risk)
    items = [_pricing(amounts=(100.0,))]
    scenario = build_scenario(
        ScenarioKind.EXPECTED, items, config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    scenario.margin_percent = margin
    scenario.roi_percent = margin * 1.25
    calculation.scenarios = [scenario]
    calculation.criteria = evaluate_criteria(
        calculation,
        scenario,
        criteria=CriteriaConfig(minimum_profit_eur=0.0),
        days_until_deadline=30,
    )
    calculation.score, calculation.verdict = score_calculation(
        calculation, scenario, scoring=ScoringConfig(), calculation_config=CONFIG
    )
    return calculation


def test_thin_coverage_yields_no_verdict_and_no_score():
    """Die Marge eines Bruchteils der Positionen ist nicht die Marge des Auftrags."""
    calculation = _scored(coverage=40, margin=35.0)
    assert calculation.verdict is Verdict.NOT_ASSESSABLE
    assert calculation.score is None


def test_good_numbers_yield_an_interesting_verdict():
    calculation = _scored(coverage=100, margin=35.0)
    assert calculation.score is not None
    assert calculation.verdict in (Verdict.VERY_INTERESTING, Verdict.INTERESTING)


def test_a_failed_criterion_beats_a_good_score():
    """Ein Mindestkriterium ist eine Entscheidung des Nutzers, keine Gewichtung."""
    calculation = _scored(coverage=100, margin=35.0, risk=95)  # Risiko ueber dem Hoechstwert
    assert calculation.score is not None and calculation.score > 40
    assert calculation.verdict is Verdict.UNSUITABLE


def test_weak_margin_lands_in_review_or_below():
    calculation = _scored(coverage=100, margin=16.0)
    assert calculation.verdict in (Verdict.REVIEW, Verdict.RATHER_UNINTERESTING)


def test_missing_risk_analysis_does_not_earn_points():
    """Kein Risikowert ist keine Entwarnung."""
    with_risk = _scored(coverage=100, margin=30.0, risk=0)
    without = _scored(coverage=100, margin=30.0, risk=None)
    assert without.score is not None and with_risk.score is not None
    assert without.score < with_risk.score


# --------------------------------------------------------------------------
# Vorlage fuer den Menschen
# --------------------------------------------------------------------------


def test_review_notes_always_state_that_this_is_no_offer():
    notes = review_notes(_calculation())
    assert any("kein Angebot" in note for note in notes)


def test_review_notes_name_the_missing_positions():
    calculation = _calculation(coverage_percent=50)
    calculation.positions = [
        position_cost(_pricing(), config=CONFIG, minimum_confidence=85),
        position_cost(_pricing(quantity=None), config=CONFIG, minimum_confidence=85),
    ]
    notes = review_notes(calculation)
    assert any("ohne belastbaren Preis" in note for note in notes)


def test_review_notes_flag_a_wide_margin_swing():
    calculation = _calculation()
    items = [_pricing(amounts=(60.0, 100.0, 140.0))]
    expected = build_scenario(
        ScenarioKind.EXPECTED, items, config=CONFIG, minimum_confidence=85, currency="EUR"
    )
    calculation.scenarios = [
        build_scenario(
            kind,
            items,
            config=CONFIG,
            minimum_confidence=85,
            currency="EUR",
            fixed_sale_total=expected.sale_total,
        )
        for kind in (ScenarioKind.BEST, ScenarioKind.WORST)
    ] + [expected]
    notes = review_notes(calculation)
    assert any("Marge schwankt" in note for note in notes)


def test_review_notes_flag_a_big_gap_to_the_published_estimate():
    calculation = _calculation(tender_estimated_value=100000.0)
    items = [_pricing()]
    calculation.scenarios = [
        build_scenario(
            ScenarioKind.EXPECTED, items, config=CONFIG, minimum_confidence=85, currency="EUR"
        )
    ]
    notes = review_notes(calculation)
    assert any("geschaetzten Auftragswert" in note for note in notes)


def test_machine_output_marks_the_result_as_no_offer():
    payload = _calculation().as_dict()
    assert payload["is_binding_offer"] is False
    assert payload["requires_user_approval"] is True
