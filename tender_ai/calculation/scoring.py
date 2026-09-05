"""Bewertung und Entscheidungsvorlage (Stufe 5).

Die Bewertung beantwortet eine Frage: lohnt es sich, diese Ausschreibung von
Hand weiterzuverfolgen? Sie beantwortet ausdruecklich **nicht** die Frage, ob
angeboten wird - das entscheidet ein Mensch.

Zwei Eigenschaften machen sie brauchbar:

* **Sie sagt Nein, wenn die Daten nicht reichen.** Eine Marge, die auf der
  Haelfte der Positionen beruht, ist keine Marge des Auftrags. Unterhalb der
  konfigurierten Abdeckung lautet das Urteil NOT_ASSESSABLE - nicht
  "uninteressant", denn das waere eine Aussage, die niemand gepruefen hat.
* **Jedes Mindestkriterium bleibt sichtbar**, mit Soll- und Ist-Wert. Wer das
  Urteil anzweifelt, sieht die Zeile, an der es haengt.
"""

from __future__ import annotations

from ..config import CalculationConfig, CriteriaConfig, ScoringConfig
from ..models.calculation import CriterionResult, Scenario, TenderCalculation, Verdict

#: Gewichte des Scores. Marge und Rendite tragen am meisten, weil sie die
#: Frage beantworten; Risiko und Abdeckung daempfen sie.
WEIGHT_MARGIN = 40
WEIGHT_ROI = 25
WEIGHT_RISK = 20
WEIGHT_COVERAGE = 15
#: Ab dieser Marge gilt der Margenanteil als voll erfuellt.
MARGIN_TARGET_PERCENT = 30.0
#: Ab dieser Rendite gilt der Renditeanteil als voll erfuellt.
ROI_TARGET_PERCENT = 40.0


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def evaluate_criteria(
    calculation: TenderCalculation,
    scenario: Scenario | None,
    *,
    criteria: CriteriaConfig,
    days_until_deadline: int | None,
) -> list[CriterionResult]:
    """Mindestkriterien pruefen - fehlende Daten gelten nie als erfuellt."""
    results: list[CriterionResult] = []

    def add(
        code: str,
        label: str,
        required: str,
        actual: float | None,
        passed: bool | None,
        *,
        suffix: str = "",
    ) -> None:
        undetermined = passed is None
        results.append(
            CriterionResult(
                code=code,
                label=label,
                required=required,
                actual="UNKNOWN" if actual is None else f"{actual:g}{suffix}",
                # Was nicht geprueft werden konnte, ist nicht bestanden -
                # sonst wuerde eine Datenluecke wie ein Erfolg aussehen.
                passed=bool(passed),
                undetermined=undetermined,
            )
        )

    margin = scenario.margin_percent if scenario else None
    add(
        "margin",
        "Mindestmarge",
        f">= {criteria.minimum_margin_percent:g} %",
        margin,
        None if margin is None else margin >= criteria.minimum_margin_percent,
        suffix=" %",
    )

    profit = scenario.margin_absolute if scenario else None
    add(
        "profit",
        "Mindestdeckungsbeitrag",
        f">= {criteria.minimum_profit_eur:g}",
        profit,
        None if profit is None else profit >= criteria.minimum_profit_eur,
    )

    roi = scenario.roi_percent if scenario else None
    add(
        "roi",
        "Mindestrendite",
        f">= {criteria.minimum_roi_percent:g} %",
        roi,
        None if roi is None else roi >= criteria.minimum_roi_percent,
        suffix=" %",
    )

    risk = calculation.risk_score
    add(
        "risk",
        "Hoechstrisiko",
        f"<= {criteria.maximum_risk_score}",
        risk,
        None if risk is None else risk <= criteria.maximum_risk_score,
    )

    add(
        "deadline",
        "Restfrist",
        f">= {criteria.minimum_days_until_deadline} Tage",
        days_until_deadline,
        None
        if days_until_deadline is None
        else (days_until_deadline >= criteria.minimum_days_until_deadline),
        suffix=" Tage",
    )
    return results


def score_calculation(
    calculation: TenderCalculation,
    scenario: Scenario | None,
    *,
    scoring: ScoringConfig,
    calculation_config: CalculationConfig,
) -> tuple[int | None, Verdict]:
    """(Score 0-100, Urteil) - beides nur bei tragfaehiger Datenlage."""
    if calculation.coverage_percent < calculation_config.minimum_coverage_percent:
        # Bewusst kein Score: eine Zahl waere hier eine Behauptung ueber
        # Positionen, zu denen nichts bekannt ist.
        return None, Verdict.NOT_ASSESSABLE
    if scenario is None or scenario.margin_percent is None:
        return None, Verdict.NOT_ASSESSABLE

    margin_part = _clamp(scenario.margin_percent / MARGIN_TARGET_PERCENT) * WEIGHT_MARGIN
    roi_part = (
        _clamp((scenario.roi_percent or 0.0) / ROI_TARGET_PERCENT) * WEIGHT_ROI
        if scenario.roi_percent is not None
        else 0.0
    )
    # Fehlendes Risiko ist keine Entwarnung: ohne Analyse gibt es hier keine
    # Punkte, statt das Risiko als null zu unterstellen.
    risk_part = (
        _clamp(1.0 - calculation.risk_score / 100.0) * WEIGHT_RISK
        if calculation.risk_score is not None
        else 0.0
    )
    coverage_part = _clamp(calculation.coverage_percent / 100.0) * WEIGHT_COVERAGE
    score = round(margin_part + roi_part + risk_part + coverage_part)

    # Ein verletztes Mindestkriterium schlaegt jeden Score: es ist eine
    # Entscheidung des Nutzers, keine Gewichtung.
    if any(not criterion.passed for criterion in calculation.criteria):
        return score, Verdict.UNSUITABLE

    thresholds = scoring.thresholds
    if score >= thresholds.very_interesting:
        return score, Verdict.VERY_INTERESTING
    if score >= thresholds.interesting:
        return score, Verdict.INTERESTING
    if score >= thresholds.review:
        return score, Verdict.REVIEW
    return score, Verdict.RATHER_UNINTERESTING


def review_notes(calculation: TenderCalculation) -> list[str]:
    """Was ein Mensch vor der Freigabe ansehen sollte.

    Die Liste ist bewusst konkret: "pruefen Sie das Ergebnis" hilft niemandem,
    "Position 1.30 beruht auf einem einzigen Angebot" schon.
    """
    notes: list[str] = []
    if calculation.coverage_percent < 100:
        missing = len(calculation.positions) - calculation.calculated_count
        notes.append(
            f"{missing} von {len(calculation.positions)} Position(en) ohne belastbaren "
            f"Preis - diese fehlen in jeder Summe."
        )

    weak = [
        position
        for position in calculation.positions
        if position.is_calculated
        and position.match_confidence is not None
        and position.match_confidence < 90
    ]
    if weak:
        listed = ", ".join(str(position.position or position.title)[:30] for position in weak[:5])
        notes.append(f"Zuordnung nicht zweifelsfrei bei: {listed}")

    best = calculation.scenario_by_name("BEST")
    worst = calculation.scenario_by_name("WORST")
    if best and worst and best.margin_percent is not None and worst.margin_percent is not None:
        spread = best.margin_percent - worst.margin_percent
        if spread > 10:
            notes.append(
                f"Marge schwankt je nach Einkaufspreis um {spread:.0f} Prozentpunkte "
                f"({worst.margin_percent:.0f} bis {best.margin_percent:.0f} Prozent) - "
                f"vor der Abgabe Einkaufspreise verbindlich klaeren."
            )

    if calculation.tender_estimated_value and calculation.expected:
        offer = calculation.expected.sale_total
        estimated = calculation.tender_estimated_value
        if estimated > 0:
            deviation = (offer - estimated) / estimated * 100.0
            if abs(deviation) >= 20:
                direction = "ueber" if deviation > 0 else "unter"
                notes.append(
                    f"Errechneter Angebotspreis liegt {abs(deviation):.0f} Prozent "
                    f"{direction} dem geschaetzten Auftragswert der Vergabestelle - "
                    f"Mengen und Leistungsumfang gegenlesen."
                )

    notes.append(
        "Diese Rechnung ist eine Entscheidungsvorlage, kein Angebot. Die Abgabe "
        "erfolgt nach Freigabe und Pruefung von Hand."
    )
    return notes
