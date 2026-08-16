"""Answerability and abstention policy engine for legal queries."""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..models import Jurisdiction, LegalEvidence, LegalProvision, LegalQuery
from ..routing import classify_jurisdiction


class AnswerabilityDecision(BaseModel):
    """Rigorous decision on whether a query can be legally answered or must abstain."""

    model_config = ConfigDict(extra="forbid")

    answerable: bool
    reason: str | None = None  # "unsupported_jurisdiction", "unknown_article", etc.
    explanation: str = ""


# Known statutory limits for Federal Decree-Law No. 33 of 2021
MAX_KNOWN_LABOUR_LAW_ARTICLE = 74
STATUTORY_ONSHORE_EFFECTIVE_DATE = date(2022, 2, 2)


def evaluate_answerability(
    query: LegalQuery,
    evidence: Sequence[LegalEvidence] | None = None,
    provisions: Sequence[LegalProvision] | None = None,
) -> AnswerabilityDecision:
    """Evaluate whether the query is legally answerable within the indexed UAE corpus."""
    # 1. Check Jurisdiction
    if query.jurisdiction in (Jurisdiction.DIFC, Jurisdiction.ADGM, Jurisdiction.OTHER_FREE_ZONE):
        return AnswerabilityDecision(
            answerable=False,
            reason="unsupported_jurisdiction",
            explanation=(
                f"The query pertains to {query.jurisdiction.value.upper()}, which operates under a separate "
                f"common law legal framework outside the UAE Federal Onshore Labour Law corpus."
            ),
        )

    query_text_cf = query.text.casefold()

    # Check for DIFC / ADGM mentions in text
    if any(fz in query_text_cf for fz in ("difc", "adgm", "financial centre", "free zone law")):
        return AnswerabilityDecision(
            answerable=False,
            reason="unsupported_jurisdiction",
            explanation="The query requests Free Zone / Financial Centre law outside UAE Federal Onshore jurisdiction.",
        )

    # 2. Check for Non-Existent Articles
    art_matches = re.findall(r"(?:article|المادة)\s*\(?(\d+)\)?", query_text_cf)
    for art_str in art_matches:
        art_num = int(art_str)
        known_articles = {int(p.article) for p in provisions or () if p.article and p.jurisdiction == Jurisdiction.UAE_ONSHORE}
        if art_num > MAX_KNOWN_LABOUR_LAW_ARTICLE or (provisions is not None and art_num not in known_articles):
            return AnswerabilityDecision(
                answerable=False,
                reason="unknown_article",
                explanation=f"Article {art_num} is not present in the indexed UAE onshore legal corpus.",
            )

    # 3. Check for Excluded / Out-of-Scope Worker Categories
    if any(cat in query_text_cf for cat in ("domestic worker", "housemaid", "nanny", "عامل منزلي", "خدمة منازل")):
        return AnswerabilityDecision(
            answerable=False,
            reason="out_of_scope_worker_category",
            explanation="Domestic workers are governed by Federal Decree-Law No. 9 of 2022, outside the core labour law corpus.",
        )

    # 4. Check Historical Temporal Window
    if query.as_of is not None and query.as_of < STATUTORY_ONSHORE_EFFECTIVE_DATE:
        return AnswerabilityDecision(
            answerable=False,
            reason="historical_corpus_gap",
            explanation=(
                f"The query specifies as_of={query.as_of.isoformat()}, which precedes the effective date "
                f"of Federal Decree-Law No. 33 of 2021 ({STATUTORY_ONSHORE_EFFECTIVE_DATE.isoformat()})."
            ),
        )

    # Jurisdiction ambiguity is checked after more specific safety reasons so
    # that an otherwise clearly out-of-scope or historically unavailable query
    # receives the actionable reason for that failure.
    if query.jurisdiction == Jurisdiction.UNKNOWN:
        routed = classify_jurisdiction(query.text)
        if routed.jurisdiction != Jurisdiction.UAE_ONSHORE:
            return AnswerabilityDecision(
                answerable=False,
                reason="jurisdiction_ambiguous",
                explanation="The applicable UAE legal regime is not established by the query.",
            )

    # 5. Check Evidence Availability if provided
    if evidence is not None and len(evidence) == 0:
        return AnswerabilityDecision(
            answerable=False,
            reason="insufficient_evidence",
            explanation="No relevant legal provisions could be retrieved for this query.",
        )

    return AnswerabilityDecision(answerable=True, reason=None, explanation="")
