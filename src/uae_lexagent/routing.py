"""Conservative pre-retrieval routing decisions."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .models import Jurisdiction


class JurisdictionDecision(BaseModel):
    jurisdiction: Jurisdiction
    confidence: float = Field(ge=0.0, le=1.0)
    matched_terms: list[str] = Field(default_factory=list)
    requires_clarification: bool
    reason: str


_DIFC_TERMS = ("difc", "dubai international financial centre")
_ADGM_TERMS = ("adgm", "abu dhabi global market")
_OTHER_FREE_ZONE_TERMS = ("free zone", "freezone", "free-zone")
_ONSHORE_TERMS = ("onshore", "mainland", "federal labour law", "federal labor law")


def _matches(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    return [term for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered)]


def classify_jurisdiction(text: str) -> JurisdictionDecision:
    """Classify only what the text establishes; geography is not jurisdiction."""

    difc = _matches(text, _DIFC_TERMS)
    adgm = _matches(text, _ADGM_TERMS)
    other = _matches(text, _OTHER_FREE_ZONE_TERMS)
    onshore = _matches(text, _ONSHORE_TERMS)

    if difc and adgm:
        return JurisdictionDecision(
            jurisdiction=Jurisdiction.UNKNOWN,
            confidence=0.99,
            matched_terms=difc + adgm,
            requires_clarification=True,
            reason="The query names multiple incompatible jurisdictions.",
        )
    if difc:
        return JurisdictionDecision(
            jurisdiction=Jurisdiction.DIFC,
            confidence=0.99,
            matched_terms=difc,
            requires_clarification=False,
            reason="DIFC was explicitly named; do not use the onshore corpus.",
        )
    if adgm:
        return JurisdictionDecision(
            jurisdiction=Jurisdiction.ADGM,
            confidence=0.99,
            matched_terms=adgm,
            requires_clarification=False,
            reason="ADGM was explicitly named; do not use the onshore corpus.",
        )
    if other:
        return JurisdictionDecision(
            jurisdiction=Jurisdiction.OTHER_FREE_ZONE,
            confidence=0.90,
            matched_terms=other,
            requires_clarification=True,
            reason="A free zone was named but no supported corpus was identified.",
        )
    if onshore:
        return JurisdictionDecision(
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            confidence=0.95,
            matched_terms=onshore,
            requires_clarification=False,
            reason="The query explicitly indicates the federal/onshore regime.",
        )

    return JurisdictionDecision(
        jurisdiction=Jurisdiction.UNKNOWN,
        confidence=0.40,
        requires_clarification=True,
        reason="Location alone is insufficient to establish the legal regime.",
    )

