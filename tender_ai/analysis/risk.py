"""Risiko-Score einer Ausschreibung (0-100).

Der Score ist bewusst **additiv und erklaerbar**: jeder Faktor bringt eine
feste Punktzahl mit, nennt seinen Grund und seine Belege. Wer die Bewertung
anzweifelt, sieht sofort, welcher Satz aus welchem Dokument dahintersteht -
ein undurchsichtiger Gesamtwert waere fuer eine Teilnahmeentscheidung
wertlos.

Wichtig: **Fehlende Information ist kein niedriges Risiko.** Wenn keine
Unterlagen lesbar sind oder Volumen und Frist fehlen, erzeugt genau das einen
eigenen Faktor - sonst wuerde eine unbekannte Ausschreibung als unauffaellig
erscheinen.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ..config import CriteriaConfig
from ..models.analysis import (
    RequirementFinding,
    RequirementKind,
    RiskAssessment,
    RiskFactor,
    RiskLevel,
)
from ..models.document import ExtractedDocument, ExtractionStatus
from ..models.tender import Tender

#: Schwellen der Einstufung. Konfigurierbar zu machen lohnt erst, wenn ein
#: Nutzer sie tatsaechlich verschieben will - dokumentiert sind sie hier.
LEVEL_THRESHOLDS: tuple[tuple[int, RiskLevel], ...] = (
    (70, RiskLevel.VERY_HIGH),
    (45, RiskLevel.HIGH),
    (20, RiskLevel.MEDIUM),
    (0, RiskLevel.LOW),
)

#: Angebotsfristen unterhalb dieser Grenzen gelten als knapp bzw. sehr knapp.
TIGHT_DEADLINE_DAYS = 14
VERY_TIGHT_DEADLINE_DAYS = 7

#: Ab so vielen Kalendertagen Zahlungsziel wird die Vorfinanzierung spuerbar.
LONG_PAYMENT_DAYS = 45

#: Weniger Text als das deutet auf duenne oder nicht maschinenlesbare
#: Unterlagen hin - die Kalkulation stuende dann auf unsicherem Grund.
THIN_CONTENT_CHARACTERS = 1_500

#: Ab so vielen Anlieferstellen wird die Logistik zum eigenen Kostenblock.
MANY_DELIVERY_SITES = 5

#: Liefertermine in weniger Tagen lassen keinen Beschaffungspuffer.
SHORT_LEAD_TIME_DAYS = 30


def _level_for(score: int) -> RiskLevel:
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return RiskLevel.LOW  # pragma: no cover - durch (0, LOW) abgedeckt


def _of_kind(
    findings: Sequence[RequirementFinding], kind: RequirementKind
) -> list[RequirementFinding]:
    return [finding for finding in findings if finding.kind is kind]


def _payment_days(findings: Sequence[RequirementFinding]) -> int | None:
    """Laengstes belegtes Zahlungsziel in Tagen."""
    days = [
        int(finding.value)
        for finding in _of_kind(findings, RequirementKind.PAYMENT_TERMS)
        if finding.value and finding.value.isdigit()
    ]
    return max(days) if days else None


def _delivery_sites(findings: Sequence[RequirementFinding]) -> int | None:
    """Groesste belegte Anzahl an Lieferorten."""
    counts = [
        int(finding.value)
        for finding in _of_kind(findings, RequirementKind.DELIVERY_TERMS)
        if finding.value and finding.value.isdigit()
    ]
    return max(counts) if counts else None


def assess_risk(
    tender: Tender,
    findings: Sequence[RequirementFinding],
    documents: Sequence[ExtractedDocument] = (),
    *,
    criteria: CriteriaConfig | None = None,
    equivalence_scope: str = "none",
    now: datetime | None = None,
) -> RiskAssessment:
    """Risikofaktoren sammeln und zum Score verdichten."""
    now = now or datetime.now(UTC)
    criteria = criteria or CriteriaConfig()
    factors: list[RiskFactor] = []

    readable = [
        document
        for document in documents
        if document.status in (ExtractionStatus.OK, ExtractionStatus.PARTIAL)
    ]
    unreadable = [document for document in documents if document not in readable]
    characters = sum(document.character_count for document in readable)

    # --- Frist -------------------------------------------------------------
    if tender.submission_deadline is not None:
        days_left = (tender.submission_deadline - now).days
        if days_left < 0:
            factors.append(
                RiskFactor(
                    code="deadline_passed",
                    label="Angebotsfrist abgelaufen",
                    points=40,
                    explanation=(
                        f"Die Angebotsfrist lag am "
                        f"{tender.submission_deadline:%d.%m.%Y} und ist vorbei."
                    ),
                )
            )
        elif days_left < VERY_TIGHT_DEADLINE_DAYS:
            factors.append(
                RiskFactor(
                    code="deadline_very_tight",
                    label="Sehr knappe Angebotsfrist",
                    points=25,
                    explanation=(
                        f"Nur noch {days_left} Tag(e) bis zur Abgabe - fuer Preisanfragen "
                        "bei Lieferanten und eine belastbare Kalkulation sehr wenig."
                    ),
                )
            )
        elif days_left < TIGHT_DEADLINE_DAYS:
            factors.append(
                RiskFactor(
                    code="deadline_tight",
                    label="Knappe Angebotsfrist",
                    points=12,
                    explanation=f"Noch {days_left} Tage bis zur Abgabe.",
                )
            )
    else:
        factors.append(
            RiskFactor(
                code="deadline_unknown",
                label="Angebotsfrist unbekannt",
                points=15,
                explanation=(
                    "Ohne Frist laesst sich weder die Beschaffungszeit noch die Teilnahme planen."
                ),
            )
        )

    # --- Wirtschaftliche Eckdaten -----------------------------------------
    if tender.estimated_value is None:
        factors.append(
            RiskFactor(
                code="value_unknown",
                label="Auftragsvolumen unbekannt",
                points=12,
                explanation=(
                    "Ohne Volumen ist die Profitabilitaet nur mit eigenen Annahmen "
                    "zu schaetzen - der Wert bleibt UNKNOWN, nicht geraten."
                ),
            )
        )

    # --- Unterlagen --------------------------------------------------------
    if not documents:
        factors.append(
            RiskFactor(
                code="documents_missing",
                label="Keine Unterlagen ausgewertet",
                points=20,
                explanation=(
                    "Es liegen keine ausgelesenen Vergabeunterlagen vor; die Bewertung "
                    "stuetzt sich allein auf die Bekanntmachung."
                ),
            )
        )
    else:
        if unreadable:
            names = ", ".join(
                document.file_name or document.source_path for document in unreadable
            )[:200]
            factors.append(
                RiskFactor(
                    code="documents_unreadable",
                    label="Unterlagen nicht auswertbar",
                    points=min(8 * len(unreadable), 24),
                    explanation=(
                        f"{len(unreadable)} Dokument(e) konnten nicht ausgelesen werden "
                        f"({names}) - moeglicherweise Scans ohne Texterkennung."
                    ),
                )
            )
        if readable and characters < THIN_CONTENT_CHARACTERS:
            factors.append(
                RiskFactor(
                    code="content_thin",
                    label="Duenne Leistungsbeschreibung",
                    points=10,
                    explanation=(
                        f"Nur {characters} Zeichen auswertbarer Text - fuer eine "
                        "belastbare Mengen- und Preisermittlung wenig."
                    ),
                )
            )

    # --- Vertragliche Risiken aus den Funden -------------------------------
    penalties = _of_kind(findings, RequirementKind.PENALTY)
    if penalties:
        factors.append(
            RiskFactor(
                code="contract_penalty",
                label="Vertragsstrafe vorgesehen",
                points=15,
                explanation=(
                    "Die Unterlagen sehen eine Vertragsstrafe vor; Lieferverzug wird "
                    "dadurch unmittelbar teuer."
                ),
                findings=penalties,
            )
        )

    securities = _of_kind(findings, RequirementKind.SECURITY)
    if securities:
        factors.append(
            RiskFactor(
                code="security_required",
                label="Sicherheitsleistung gefordert",
                points=12,
                explanation=(
                    "Eine Buergschaft oder Sicherheitsleistung bindet Kapital und "
                    "verursacht Avalkosten."
                ),
                findings=securities,
            )
        )

    certifications = _of_kind(findings, RequirementKind.CERTIFICATION)
    unique_certificates = {(finding.value or finding.text).casefold() for finding in certifications}
    if len(unique_certificates) >= 3:
        factors.append(
            RiskFactor(
                code="many_certifications",
                label="Hohe Zertifizierungsanforderungen",
                points=min(4 * len(unique_certificates), 20),
                explanation=(
                    f"{len(unique_certificates)} unterschiedliche Zertifikate bzw. Normen "
                    "gefordert - jedes fehlende ist ein Ausschlussgrund."
                ),
                findings=certifications[:5],
            )
        )
    elif unique_certificates:
        factors.append(
            RiskFactor(
                code="certifications_required",
                label="Zertifikate gefordert",
                points=5,
                explanation="Nachweise sind beizubringen; Verfuegbarkeit pruefen.",
                findings=certifications[:3],
            )
        )

    brand_locks = _of_kind(findings, RequirementKind.BRAND_LOCK)
    if brand_locks:
        # Dreistufig: eine Klausel direkt an der Fabrikatsangabe oeffnet die
        # Vorgabe wirklich; eine Generalklausel irgendwo im Dokument mildert
        # sie nur - sonst wuerde ein "oder gleichwertig" auf Seite 3 eine
        # strikte Vorgabe auf Seite 80 entschaerfen.
        if equivalence_scope == "inline":
            factors.append(
                RiskFactor(
                    code="brand_lock_with_equivalence",
                    label="Herstellervorgabe mit Gleichwertigkeitsklausel",
                    points=5,
                    explanation=(
                        "Ein Fabrikat ist vorgegeben, im selben Satz jedoch Gleichwertiges "
                        "zugelassen - die Gleichwertigkeit ist zu belegen."
                    ),
                    findings=brand_locks[:3],
                )
            )
        elif equivalence_scope == "document":
            factors.append(
                RiskFactor(
                    code="brand_lock_general_clause",
                    label="Herstellervorgabe, Gleichwertigkeit nur allgemein zugelassen",
                    points=10,
                    explanation=(
                        "Ein Fabrikat ist vorgegeben; eine Gleichwertigkeitsklausel steht "
                        "an anderer Stelle im Dokument - ob sie fuer diese Position gilt, "
                        "ist zu pruefen."
                    ),
                    findings=brand_locks[:3],
                )
            )
        else:
            factors.append(
                RiskFactor(
                    code="brand_lock_strict",
                    label="Herstellerbindung ohne Gleichwertigkeitsklausel",
                    points=18,
                    explanation=(
                        "Ein bestimmtes Fabrikat wird verlangt, ohne 'oder gleichwertig' "
                        "zuzulassen - der Einkauf ist damit auf eine Quelle festgelegt."
                    ),
                    findings=brand_locks[:3],
                )
            )

    sites = _delivery_sites(findings)
    if sites is not None and sites >= MANY_DELIVERY_SITES:
        factors.append(
            RiskFactor(
                code="delivery_many_sites",
                label="Lieferung an viele Standorte",
                points=min(2 * (sites // 5), 15),
                explanation=(
                    f"Die Ware ist an {sites} Stellen anzuliefern - Logistik und "
                    "Abwicklung kosten deutlich mehr als eine Sammellieferung."
                ),
                findings=_of_kind(findings, RequirementKind.DELIVERY_TERMS)[:2],
            )
        )

    if tender.delivery_deadline is not None:
        lead_days = (tender.delivery_deadline - now.date()).days
        if lead_days < SHORT_LEAD_TIME_DAYS:
            factors.append(
                RiskFactor(
                    code="delivery_lead_time_short",
                    label="Kurze Lieferfrist",
                    points=15 if lead_days < SHORT_LEAD_TIME_DAYS // 2 else 8,
                    explanation=(
                        f"Zwischen heute und dem Liefertermin liegen {lead_days} Tage - "
                        "Beschaffung und Anlieferung muessen ohne Puffer klappen."
                    ),
                )
            )

    payment_days = _payment_days(findings)
    if payment_days is not None and payment_days >= LONG_PAYMENT_DAYS:
        factors.append(
            RiskFactor(
                code="payment_terms_long",
                label="Langes Zahlungsziel",
                points=10,
                explanation=(
                    f"Zahlungsziel von {payment_days} Tagen - die Ware ist so lange "
                    "vorzufinanzieren."
                ),
                findings=_of_kind(findings, RequirementKind.PAYMENT_TERMS)[:2],
            )
        )

    minimums = _of_kind(findings, RequirementKind.MINIMUM)
    if len(minimums) >= 5:
        factors.append(
            RiskFactor(
                code="many_minimum_requirements",
                label="Viele Mindestanforderungen",
                points=8,
                explanation=(
                    f"{len(minimums)} ausdrueckliche Mindest- bzw. Ausschlusskriterien - "
                    "jedes einzelne muss erfuellt sein."
                ),
                findings=minimums[:5],
            )
        )

    if not _of_kind(findings, RequirementKind.AWARD_CRITERIA) and documents:
        factors.append(
            RiskFactor(
                code="award_criteria_unclear",
                label="Zuschlagskriterien nicht erkennbar",
                points=8,
                explanation=(
                    "In den ausgewerteten Unterlagen sind keine Zuschlagskriterien "
                    "gefunden worden - unklar, worauf das Angebot optimiert werden muss."
                ),
            )
        )

    score = min(100, sum(factor.points for factor in factors))
    return RiskAssessment(
        tender_id=tender.id,
        score=score,
        level=_level_for(score),
        factors=factors,
        documents_analyzed=len(readable),
        documents_unreadable=len(unreadable),
        characters_analyzed=characters,
        computed_at=now,
    )
