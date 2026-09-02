"""Anforderungen aus den ausgelesenen Unterlagen erkennen.

Bewusst regelbasiert und zurueckhaltend: erkannt wird, was sich an klaren
Signalwoertern deutscher Vergabeunterlagen festmachen laesst. Jeder Fund
enthaelt den Satz, in dem er steht, sowie Dokument und Seite - damit bleibt
jede spaetere Bewertung nachpruefbar.

Ein Treffer ist ein *Hinweis*, keine Rechtsauskunft: die Confidence liegt
deshalb nie bei 100, und die Entscheidungsvorlage weist die Funde als "zu
pruefen" aus.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from ..models.analysis import RequirementFinding, RequirementKind
from ..models.common import Provenance
from ..models.document import ExtractedDocument
from ..models.tender import TenderRequirements

#: Ein Satz endet an .!?; oder am Zeilenumbruch. Absichtlich simpel - der Satz
#: dient als Beleg, nicht als linguistische Analyse.
_SENTENCE = re.compile(r"[^.!?;\n]+[.!?;]?")

#: Maximale Laenge eines Belegs; laengere Saetze werden gekuerzt.
MAX_EVIDENCE_CHARS = 400


class Rule:
    """Eine Erkennungsregel: Muster, Art des Funds, Confidence."""

    def __init__(
        self,
        kind: RequirementKind,
        pattern: str,
        *,
        label: str,
        confidence: int = 70,
        value_group: int | None = None,
    ) -> None:
        self.kind = kind
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.label = label
        self.confidence = confidence
        self.value_group = value_group


#: Normen und Zertifikate werden zusaetzlich als normierter Wert gefuehrt,
#: damit Stufe 5 pruefen kann, ob wir sie besitzen.
_NORM_PATTERN = r"(?:DIN\s+)?(?:EN\s+)?ISO\s*\d{3,5}(?:[-:]\d+)?"

RULES: tuple[Rule, ...] = (
    Rule(
        RequirementKind.CERTIFICATION,
        rf"({_NORM_PATTERN})",
        label="Norm/Zertifikat",
        confidence=85,
        value_group=1,
    ),
    # Konkrete Guetezeichen zuerst: im Satz "Umweltzeichen Blauer Engel" soll
    # der normierte Wert das Zeichen sein, nicht der Oberbegriff.
    Rule(
        RequirementKind.CERTIFICATION,
        r"\b(CE-Kennzeichnung|CE-Konformitaet|CE-Konformität|GS-Zeichen|Blauer Engel"
        r"|EMAS|TUEV|TÜV|Energy Star|EPEAT|FSC|PEFC)\b",
        label="Zertifikat/Guetezeichen",
        confidence=80,
        value_group=1,
    ),
    Rule(
        RequirementKind.CERTIFICATION,
        r"\b(Umweltzeichen|Guetezeichen|Gütezeichen|Pruefzeichen|Prüfzeichen)\b",
        label="Guetezeichen (Oberbegriff)",
        confidence=60,
    ),
    Rule(
        RequirementKind.CERTIFICATION,
        r"\b(Zertifikat\w*|Zertifizierung\w*|Konformitaetserklaerung|Konformitätserklärung"
        r"|Nachweis\w* der Zertifizierung)\b",
        label="Zertifikatsanforderung",
        confidence=65,
    ),
    Rule(
        RequirementKind.ELIGIBILITY,
        r"\b(Eignungsnachweis\w*|Praequalifikation|Präqualifikation|Referenz\w*"
        r"|Unbedenklichkeitsbescheinigung|Handelsregisterauszug|Berufshaftpflicht\w*"
        r"|Mindestjahresumsatz|Eigenerklaerung|Eigenerklärung)\b",
        label="Eignungsanforderung",
        confidence=75,
    ),
    Rule(
        RequirementKind.MINIMUM,
        r"\b(Mindestanforderung\w*|Mindestens|zwingend erforderlich|zwingend einzuhalten"
        r"|Ausschlusskriterium|K\.?-?O\.?-?Kriterium)\b",
        label="Mindestanforderung",
        confidence=75,
    ),
    Rule(
        RequirementKind.BRAND_LOCK,
        r"\b(Fabrikat|Herstellervorgabe|Originalprodukt\w*|Originalersatzteil\w*"
        r"|nur Produkte des Herstellers|keine Alternativprodukte"
        r"|Nachbauprodukte sind ausgeschlossen)\b",
        label="Hersteller-/Markenbindung",
        confidence=70,
    ),
    Rule(
        RequirementKind.PAYMENT_TERMS,
        r"Zahlungsziel[^.\n]{0,40}?(\d{1,3})\s*(?:Kalender|Werk)?tag",
        label="Zahlungsziel",
        confidence=85,
        value_group=1,
    ),
    Rule(
        RequirementKind.PAYMENT_TERMS,
        r"\b(Vorkasse|Vorauszahlung|Skonto|Abschlagszahlung\w*|Zahlung nach Abnahme)\b",
        label="Zahlungsbedingung",
        confidence=70,
    ),
    Rule(
        RequirementKind.DELIVERY_TERMS,
        r"\b(frei Haus|frei Verwendungsstelle|DDP|DAP|Anlieferung erfolgt"
        r"|Lieferung frei|Abladestelle)\b",
        label="Lieferbedingung",
        confidence=75,
    ),
    Rule(
        RequirementKind.AWARD_CRITERIA,
        r"\b(Zuschlagskriteri\w+|wirtschaftlichste\w* Angebot|Wertungskriteri\w+"
        r"|Bewertungsmatrix|niedrigste\w* Preis)\b",
        label="Zuschlagskriterium",
        confidence=75,
    ),
    Rule(
        RequirementKind.WARRANTY,
        r"\b(Gewaehrleistung\w*|Gewährleistung\w*|Sachmaengelhaftung|Sachmängelhaftung"
        r"|Garantie\w*|Vor-Ort-Service|Reaktionszeit)\b",
        label="Gewaehrleistung/Service",
        confidence=70,
    ),
    Rule(
        RequirementKind.PENALTY,
        r"\b(Vertragsstrafe\w*|Konventionalstrafe|Poenale|Pönale|Schadenspauschale)\b",
        label="Vertragsstrafe",
        confidence=85,
    ),
    Rule(
        RequirementKind.SECURITY,
        r"\b(Sicherheitsleistung|Vertragserfuellungsbuergschaft"
        r"|Vertragserfüllungsbürgschaft|Buergschaft|Bürgschaft|Gewaehrleistungssicherheit)\b",
        label="Sicherheitsleistung",
        confidence=85,
    ),
    Rule(
        RequirementKind.DELIVERY_TERMS,
        r"(\d{1,4})\s*(?:verschiedene\s+)?"
        r"(?:Abladestellen|Lieferorte|Lieferstellen|Liegenschaften|Standorte|Dienststellen)",
        label="Anzahl Lieferorte",
        confidence=80,
        value_group=1,
    ),
    Rule(
        RequirementKind.TECHNICAL,
        r"\b(technische Spezifikation\w*|Leistungsbeschreibung|Leistungsverzeichnis"
        r"|technische Anforderung\w*|Datenblatt)\b",
        label="Technische Spezifikation",
        confidence=60,
    ),
)

#: Formulierungen, die eine Markenvorgabe wieder oeffnen. Fehlen sie neben
#: einer Fabrikatsangabe, ist die Bindung eng - das ist ein Risikosignal.
EQUIVALENCE_PATTERN = re.compile(
    r"oder\s+gleichwertig|gleichwertige\w*\s+(?:Produkt|Fabrikat|Alternative)"
    r"|bzw\.\s+gleichwertig",
    re.IGNORECASE,
)


def _sentences(text: str) -> Iterator[str]:
    for match in _SENTENCE.finditer(text):
        sentence = " ".join(match.group(0).split())
        if len(sentence) >= 8:
            yield sentence


def _shorten(sentence: str) -> str:
    if len(sentence) <= MAX_EVIDENCE_CHARS:
        return sentence
    return sentence[: MAX_EVIDENCE_CHARS - 1].rstrip() + "…"


def extract_requirements(
    documents: Iterable[ExtractedDocument], *, max_findings_per_kind: int = 25
) -> list[RequirementFinding]:
    """Hinweise in den ausgelesenen Unterlagen suchen.

    Dubletten (derselbe Satz, dieselbe Regel) werden zusammengefasst; je Art
    werden hoechstens ``max_findings_per_kind`` Funde behalten, damit ein
    200-seitiges Leistungsverzeichnis die Auswertung nicht flutet.
    """
    findings: list[RequirementFinding] = []
    seen: set[tuple[str, str]] = set()
    counts: dict[RequirementKind, int] = {}

    for document in documents:
        for page in document.pages:
            for sentence in _sentences(page.text):
                for rule in RULES:
                    match = rule.regex.search(sentence)
                    if match is None:
                        continue
                    key = (str(rule.kind), sentence.casefold())
                    if key in seen:
                        continue
                    if counts.get(rule.kind, 0) >= max_findings_per_kind:
                        continue
                    seen.add(key)
                    counts[rule.kind] = counts.get(rule.kind, 0) + 1

                    value = None
                    if rule.value_group is not None:
                        try:
                            value = " ".join(match.group(rule.value_group).split())
                        except IndexError:  # pragma: no cover - Regex ohne Gruppe
                            value = None
                    findings.append(
                        RequirementFinding(
                            kind=rule.kind,
                            text=rule.label,
                            value=value,
                            confidence=rule.confidence,
                            provenance=Provenance(
                                source=document.extractor or "document",
                                method="document",
                                document=document.file_name or document.source_path,
                                page=page.number,
                                original_text=_shorten(sentence),
                                confidence=rule.confidence,
                            ),
                        )
                    )
    return findings


def has_equivalence_clause(documents: Iterable[ExtractedDocument]) -> bool:
    """Enthaelt mindestens ein Dokument irgendwo eine Gleichwertigkeitsklausel?

    Viele Leistungsverzeichnisse stellen eine Generalklausel voran ("alle
    Fabrikatsangaben verstehen sich als 'oder gleichwertig'"). Das entschaerft
    eine Markenvorgabe - aber schwaecher als eine Klausel direkt an der
    Fabrikatsangabe, siehe ``equivalence_scope``.
    """
    return any(EQUIVALENCE_PATTERN.search(document.text) for document in documents)


def equivalence_scope(
    documents: Iterable[ExtractedDocument], brand_findings: Iterable[RequirementFinding]
) -> str:
    """Wie weit reicht eine Gleichwertigkeitsklausel?

    - ``"inline"``: sie steht im selben Satz wie die Fabrikatsangabe - die
      Vorgabe ist damit eindeutig geoeffnet
    - ``"document"``: sie steht irgendwo im Dokument - wahrscheinlich eine
      Generalklausel, die die Bindung mildert, aber nicht aufhebt
    - ``"none"``: keine Klausel gefunden - die Bindung ist eng

    Die Unterscheidung ist wichtig, weil sonst ein einziges "oder gleichwertig"
    auf Seite 3 eine strikte Vorgabe auf Seite 80 entschaerfen wuerde.
    """
    findings = list(brand_findings)
    if findings and all(EQUIVALENCE_PATTERN.search(finding.evidence()) for finding in findings):
        return "inline"
    if any(EQUIVALENCE_PATTERN.search(document.text) for document in documents):
        return "document"
    return "none"


def findings_to_requirements(findings: Iterable[RequirementFinding]) -> TenderRequirements:
    """Funde in das ``TenderRequirements``-Modell ueberfuehren.

    Es werden nur belegte Angaben uebernommen; leere Felder bleiben leer statt
    mit Vermutungen gefuellt zu werden.
    """
    requirements = TenderRequirements()
    certifications: list[str] = []
    for finding in findings:
        evidence = finding.evidence()
        match finding.kind:
            case RequirementKind.CERTIFICATION:
                certifications.append(finding.value or evidence)
            case RequirementKind.ELIGIBILITY:
                requirements.eligibility.append(evidence)
            case RequirementKind.TECHNICAL:
                requirements.technical.append(evidence)
            case RequirementKind.MINIMUM:
                requirements.minimum.append(evidence)
            case RequirementKind.AWARD_CRITERIA:
                requirements.award_criteria.append(evidence)
            case RequirementKind.DELIVERY_TERMS:
                if requirements.delivery_terms is None:
                    requirements.delivery_terms = evidence
            case RequirementKind.PAYMENT_TERMS:
                if requirements.payment_terms is None:
                    requirements.payment_terms = evidence
            case _:
                continue

    # Normierte Zertifikatsnamen zuerst, Rest ohne Dubletten anhaengen.
    seen: set[str] = set()
    for entry in certifications:
        key = entry.casefold()
        if key not in seen:
            seen.add(key)
            requirements.certifications.append(entry)
    return requirements
