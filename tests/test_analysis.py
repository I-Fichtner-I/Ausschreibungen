"""Stufe 2B: Anforderungserkennung und Risiko-Score."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tender_ai.analysis import (
    assess_risk,
    equivalence_scope,
    extract_requirements,
    findings_to_requirements,
)
from tender_ai.analysis.risk import LEVEL_THRESHOLDS
from tender_ai.models.analysis import RequirementKind, RiskLevel
from tender_ai.models.document import ExtractedDocument, ExtractedPage, ExtractionStatus
from tender_ai.models.tender import Tender

FUTURE = datetime(2036, 10, 15, 12, tzinfo=UTC)
NOW = datetime(2036, 1, 1, tzinfo=UTC)


def document(*texts: str, status: ExtractionStatus = ExtractionStatus.OK) -> ExtractedDocument:
    extracted = ExtractedDocument(
        source_path="lv.pdf", file_name="lv.pdf", extractor="pdf", status=status
    )
    for number, text in enumerate(texts, start=1):
        extracted.pages.append(ExtractedPage(number=number, text=text))
    return extracted


def tender(**overrides) -> Tender:
    base = dict(
        id="ted:1",
        source="ted",
        source_id="1",
        title="Lieferung von Monitoren",
        submission_deadline=FUTURE,
        estimated_value=420000.0,
    )
    base.update(overrides)
    return Tender(**base)


# --- Erkennung ------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        (
            "Die Geraete muessen DIN EN ISO 9241-307 entsprechen.",
            RequirementKind.CERTIFICATION,
            "DIN EN ISO 9241-307",
        ),
        ("Ein Zertifikat nach ISO 9001 ist vorzulegen.", RequirementKind.CERTIFICATION, "ISO 9001"),
        (
            "Gefordert wird das Umweltzeichen Blauer Engel.",
            RequirementKind.CERTIFICATION,
            "Blauer Engel",
        ),
        (
            "Zahlungsziel betraegt 60 Tage nach Rechnungseingang.",
            RequirementKind.PAYMENT_TERMS,
            "60",
        ),
        ("Die Lieferung erfolgt an 12 Abladestellen.", RequirementKind.DELIVERY_TERMS, "12"),
        ("Bei Verzug wird eine Vertragsstrafe faellig.", RequirementKind.PENALTY, None),
        ("Eine Vertragserfuellungsbuergschaft ist zu stellen.", RequirementKind.SECURITY, None),
        ("Mindestanforderung: Reaktionszeit 24 Stunden.", RequirementKind.MINIMUM, None),
        ("Ein Eignungsnachweis ist beizubringen.", RequirementKind.ELIGIBILITY, None),
        (
            "Zuschlagskriterien: Preis 70 Prozent, Qualitaet 30 Prozent.",
            RequirementKind.AWARD_CRITERIA,
            None,
        ),
    ],
)
def test_rules_find_expected_kind_and_value(text: str, kind: RequirementKind, value: str | None):
    findings = extract_requirements([document(text)])
    matching = [finding for finding in findings if finding.kind is kind]
    assert matching, f"'{text}' haette {kind} liefern muessen"
    if value is not None:
        assert any(finding.value == value for finding in matching)


def test_every_finding_carries_its_source():
    """Ohne Fundstelle ist ein Fund fuer die Entscheidungsvorlage wertlos."""
    findings = extract_requirements(
        [document("Seite eins ohne Treffer.", "Eine Vertragsstrafe ist vorgesehen.")]
    )
    assert findings
    for finding in findings:
        assert finding.provenance is not None
        assert finding.provenance.document == "lv.pdf"
        assert finding.provenance.page in (1, 2)
        assert finding.provenance.original_text
        assert 0 < finding.confidence <= 100  # nie 100: ein Stichwort ist ein Hinweis


def test_duplicates_are_collapsed_and_capped():
    text = " ".join(["Eine Vertragsstrafe ist vorgesehen."] * 40)
    findings = extract_requirements([document(text)], max_findings_per_kind=3)
    penalties = [f for f in findings if f.kind is RequirementKind.PENALTY]
    assert len(penalties) == 1  # identische Saetze zaehlen einmal

    varied = " ".join(f"Vertragsstrafe Variante {i} gilt hier." for i in range(40))
    capped = extract_requirements([document(varied)], max_findings_per_kind=3)
    assert len([f for f in capped if f.kind is RequirementKind.PENALTY]) == 3


def test_no_findings_on_unrelated_text():
    findings = extract_requirements([document("Heute scheint die Sonne in Musterstadt.")])
    assert findings == []


def test_findings_become_requirements_without_invention():
    findings = extract_requirements(
        [
            document(
                "Ein Zertifikat nach ISO 9001 ist vorzulegen. "
                "Zahlungsziel betraegt 30 Tage. "
                "Zuschlagskriterien: niedrigster Preis."
            )
        ]
    )
    requirements = findings_to_requirements(findings)
    assert "ISO 9001" in requirements.certifications
    assert requirements.payment_terms and "30 Tage" in requirements.payment_terms
    assert requirements.award_criteria
    # Nicht belegt = leer, nicht geraten
    assert requirements.price_weight_percent is None
    assert requirements.minimum == []


# --- Gleichwertigkeitsklausel ---------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected_scope", "expected_code"),
    [
        (
            "Fabrikat: Muster MX-27 oder gleichwertig ist anzubieten.",
            "inline",
            "brand_lock_with_equivalence",
        ),
        (
            (
                "Alle Fabrikatsangaben verstehen sich als oder gleichwertig. "
                "Fabrikat: Muster MX-27 wird gefordert."
            ),
            "document",
            "brand_lock_general_clause",
        ),
        (
            "Fabrikat: Muster MX-27. Nachbauprodukte sind ausgeschlossen.",
            "none",
            "brand_lock_strict",
        ),
    ],
)
def test_equivalence_clause_is_weighted_by_scope(text, expected_scope, expected_code):
    """Eine Klausel auf Seite 3 darf keine strikte Vorgabe auf Seite 80 entschaerfen."""
    extracted = document(text)
    findings = extract_requirements([extracted])
    brands = [f for f in findings if f.kind is RequirementKind.BRAND_LOCK]
    assert brands
    scope = equivalence_scope([extracted], brands)
    assert scope == expected_scope

    risk = assess_risk(tender(), findings, [extracted], equivalence_scope=scope, now=NOW)
    codes = [factor.code for factor in risk.factors]
    assert expected_code in codes


# --- Risiko ---------------------------------------------------------------
def test_missing_information_raises_risk_instead_of_lowering_it():
    """Eine unbekannte Ausschreibung darf nicht als unauffaellig erscheinen."""
    risk = assess_risk(tender(submission_deadline=None, estimated_value=None), [], [], now=NOW)
    codes = {factor.code for factor in risk.factors}
    assert {"deadline_unknown", "value_unknown", "documents_missing"} <= codes
    assert risk.score > 0
    assert risk.level is not RiskLevel.LOW


def test_clean_tender_scores_low():
    text = (
        "Zuschlagskriterien: Preis 70 Prozent, Qualitaet 30 Prozent. "
        "Die Lieferung erfolgt frei Haus. " + "Ausfuehrliche Leistungsbeschreibung. " * 60
    )
    extracted = document(text)
    findings = extract_requirements([extracted])
    risk = assess_risk(tender(), findings, [extracted], now=NOW)
    assert risk.score < 20
    assert risk.level is RiskLevel.LOW


@pytest.mark.parametrize(
    ("deadline_days", "expected_code"),
    [(-1, "deadline_passed"), (3, "deadline_very_tight"), (10, "deadline_tight"), (60, None)],
)
def test_deadline_pressure(deadline_days: int, expected_code: str | None):
    subject = tender(submission_deadline=NOW + timedelta(days=deadline_days))
    risk = assess_risk(subject, [], [document("Text")], now=NOW)
    codes = {factor.code for factor in risk.factors}
    if expected_code:
        assert expected_code in codes
    else:
        assert not any(code.startswith("deadline") for code in codes)


def test_unreadable_documents_are_a_risk_factor():
    scan = document("", status=ExtractionStatus.EMPTY)
    risk = assess_risk(tender(), [], [scan], now=NOW)
    factor = next(f for f in risk.factors if f.code == "documents_unreadable")
    assert "lv.pdf" in factor.explanation
    assert risk.documents_unreadable == 1
    assert risk.documents_analyzed == 0


def test_short_lead_time_and_many_sites():
    extracted = document("Die Lieferung erfolgt an 20 Abladestellen.")
    findings = extract_requirements([extracted])
    subject = tender(delivery_deadline=NOW.date() + timedelta(days=10))
    risk = assess_risk(subject, findings, [extracted], now=NOW)
    codes = {factor.code for factor in risk.factors}
    assert "delivery_lead_time_short" in codes
    assert "delivery_many_sites" in codes


def test_score_is_capped_and_explained():
    text = (
        "Fabrikat: Muster MX-27. Nachbauprodukte sind ausgeschlossen. "
        "Eine Vertragsstrafe wird faellig. Eine Buergschaft ist zu stellen. "
        "Zertifikat nach ISO 9001, DIN EN ISO 14001 und EMAS erforderlich. "
        "Zahlungsziel betraegt 90 Tage. "
        "Mindestanforderung A. Mindestanforderung B. Mindestanforderung C. "
        "Mindestanforderung D. Mindestanforderung E. "
    )
    extracted = document(text)
    findings = extract_requirements([extracted])
    risk = assess_risk(
        tender(submission_deadline=NOW + timedelta(days=2)),
        findings,
        [extracted],
        equivalence_scope="none",
        now=NOW,
    )
    assert risk.score == 100  # gedeckelt
    assert risk.level is RiskLevel.VERY_HIGH
    # Jeder Faktor muss begruendet sein - ein nackter Zahlenwert hilft niemandem
    for factor in risk.factors:
        assert factor.explanation
        assert factor.points > 0
    assert risk.top_factors[0].points >= risk.top_factors[-1].points


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0, RiskLevel.LOW),
        (19, RiskLevel.LOW),
        (20, RiskLevel.MEDIUM),
        (45, RiskLevel.HIGH),
        (70, RiskLevel.VERY_HIGH),
        (100, RiskLevel.VERY_HIGH),
    ],
)
def test_level_thresholds(score: int, level: RiskLevel):
    from tender_ai.analysis.risk import _level_for

    assert _level_for(score) is level
    assert LEVEL_THRESHOLDS[-1] == (0, RiskLevel.LOW)


def test_assessment_serialises_with_evidence():
    extracted = document("Eine Vertragsstrafe wird faellig.")
    findings = extract_requirements([extracted])
    risk = assess_risk(tender(), findings, [extracted], now=NOW)
    payload = risk.as_dict()
    penalty = next(f for f in payload["factors"] if f["code"] == "contract_penalty")
    assert penalty["evidence"] and "Vertragsstrafe" in penalty["evidence"][0]
    assert payload["score"] == risk.score
    assert isinstance(payload["computed_at"], str)


def test_publication_date_is_not_required(recwarn):
    """Fehlt das Veroeffentlichungsdatum, darf die Bewertung trotzdem laufen."""
    subject = tender(publication_date=None)
    assert assess_risk(subject, [], [document("Text")], now=NOW).score >= 0
    assert date  # Import bleibt genutzt
