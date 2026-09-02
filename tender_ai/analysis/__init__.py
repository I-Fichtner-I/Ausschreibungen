"""Analyse der Vergabeunterlagen (Stufe 2B).

Aufbau: ``requirements`` erkennt Hinweise im Text, ``risk`` bewertet sie.
Beide arbeiten regelbasiert und deterministisch - jeder Fund traegt seine
Fundstelle, jeder Risikopunkt seine Begruendung. Eine KI-gestuetzte Ergaenzung
(Anforderung 22) kann spaeter zusaetzliche Funde mit eigener Confidence
beisteuern, ohne dass sich die Schnittstelle aendert.
"""

from .requirements import (
    equivalence_scope,
    extract_requirements,
    findings_to_requirements,
)
from .risk import assess_risk

__all__ = [
    "assess_risk",
    "equivalence_scope",
    "extract_requirements",
    "findings_to_requirements",
]
