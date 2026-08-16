"""Citation verification gate and statutory fact-checking engine."""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Sequence

from ..models import LegalEvidence, LegalQuery
from .contracts import (
    DraftLegalClaim,
    GeneratedLegalAnswer,
    GroundedLegalAnswer,
    VerificationStatus,
    VerifiedLegalClaim,
)
from .facts import extract_legal_facts

_CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_]+)\]")

_ALIGNMENT_CLAIM_KEYS: dict[str, set[str]] = {
    "FDL_33_2021_A9_C1": {"probation_max_months", "probation_employer_notice_days"},
    "FDL_33_2021_A9_C2": {"probation_continuation_service_credit"},
    "FDL_33_2021_A9_C3": {"probation_employee_transfer_notice_months", "probation_recruitment_compensation"},
    "FDL_33_2021_A9_C4": {"probation_employee_departure_notice_days"},
    "FDL_33_2021_A10_C1": {"non_compete_max_years"},
    "FDL_33_2021_A51_C2": {"gratuity_calculation"},
    "FDL_33_2021_A51_C6": {"gratuity_calculation", "gratuity_cap_years"},
}


class CitationVerifier:
    """Rigorous gate checking every claim against retrieved evidence before final answer release."""

    def __init__(
        self,
        valid_corpus_alignment_ids: set[str] | None = None,
        strict: bool = True,
    ) -> None:
        self.valid_corpus_alignment_ids = valid_corpus_alignment_ids
        self.strict = strict

    def verify(
        self,
        query: LegalQuery,
        draft: GeneratedLegalAnswer,
        evidence: Sequence[LegalEvidence],
    ) -> GroundedLegalAnswer:
        start_time = time.perf_counter()

        # If draft is already marked unanswerable / abstained, never release its
        # generated prose: even an abstention draft may contain unsupported text.
        if not draft.answerable or draft.abstention_reason:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return GroundedLegalAnswer(
                answer_id=draft.answer_id,
                query=query,
                jurisdiction=draft.jurisdiction,
                answerable=False,
                verification_status=VerificationStatus.ABSTAINED,
                summary="The answer was withheld because it could not be established from the supported legal corpus.",
                claims=[],
                full_text="**Abstention Notice**: The answer could not be verified against the supported legal corpus.",
                referenced_evidence=[],
                abstention_reason=draft.abstention_reason or "policy_abstention",
                generation_latency_ms=draft.generation_latency_ms,
                verification_latency_ms=latency_ms,
            )

        # Build evidence map
        evidence_by_align: dict[str, LegalEvidence] = {}
        for ev in evidence:
            align_id = ev.provision.alignment_id or ev.provision.provision_id
            evidence_by_align[align_id] = ev

        valid_ids = self.valid_corpus_alignment_ids
        if valid_ids is None:
            valid_ids = set()

        # Structured claims are the only permitted source of rendered citations.
        rendered_citations = set(_CITATION_PATTERN.findall(draft.full_text))
        structured_citations = {
            cid for claim in draft.claims for cid in claim.cited_alignment_ids
        }
        citation_format_error = bool(rendered_citations - structured_citations)

        verified_claims: list[VerifiedLegalClaim] = []
        has_contradiction = False
        all_verified = bool(draft.claims)

        for claim in draft.claims:
            reasons: list[str] = []
            status = VerificationStatus.VERIFIED
            support_score = 1.0

            # 1. Check Citation Existence
            if not claim.cited_alignment_ids:
                status = VerificationStatus.UNVERIFIED
                reasons.append("Claim lacks statutory citation.")
                support_score = 0.0
            else:
                for cid in claim.cited_alignment_ids:
                    # Check official corpus validity
                    if cid not in valid_ids:
                        status = VerificationStatus.UNVERIFIED
                        reasons.append(f"Citation '{cid}' does not exist in official corpus.")
                        support_score = 0.0

                    # Check retrieved evidence presence
                    if cid not in evidence_by_align:
                        status = VerificationStatus.UNVERIFIED
                        reasons.append(f"Citation '{cid}' is absent from retrieved evidence.")
                        support_score = 0.0

            # 2. Check Claim Content vs Evidence Facts
            if status == VerificationStatus.VERIFIED:
                if not any(claim.claim_key in _ALIGNMENT_CLAIM_KEYS.get(cid, set()) for cid in claim.cited_alignment_ids):
                    status = VerificationStatus.UNVERIFIED
                    reasons.append("Claim meaning is not supported by the cited statutory provision.")
                    support_score = 0.0

                claim_facts = extract_legal_facts(claim.conclusion)
                cited_facts = [
                    extract_legal_facts(evidence_by_align[cid].provision.text)[0]
                    for cid in claim.cited_alignment_ids
                ]

                if status == VerificationStatus.VERIFIED and claim_facts and cited_facts:
                    cf = claim_facts[0]
                    evidence_quantities = {
                        (quantity.value, quantity.unit)
                        for fact in cited_facts
                        for quantity in fact.quantities
                    }
                    def quantity_supported(quantity: object) -> bool:
                        value = quantity.value
                        unit = quantity.unit
                        return (value, unit) in evidence_quantities

                    # Multi-clause claims are supported collectively by their
                    # citations, such as gratuity calculation plus its cap.
                    if not all(quantity_supported(quantity) for quantity in cf.quantities):
                        status = VerificationStatus.CONTRADICTED
                        reasons.append("Numeric quantity/unit mismatch between claim and evidence.")
                        support_score = 0.0
                        has_contradiction = True

            if status != VerificationStatus.VERIFIED:
                all_verified = False
            else:
                reasons.append("Statutory citation and facts verified against official provision.")

            verified_claims.append(
                VerifiedLegalClaim(
                    claim_id=claim.claim_id,
                    claim_key=claim.claim_key,
                    conclusion=claim.conclusion,
                    cited_alignment_ids=claim.cited_alignment_ids,
                    confidence=claim.confidence if status == VerificationStatus.VERIFIED else Decimal("0.0"),
                    verification_status=status,
                    verification_reasons=reasons,
                    support_score=support_score,
                )
            )

        # 3. Determine Final Answer Status
        final_answerable = True
        overall_status = VerificationStatus.VERIFIED
        abstention_reason = None

        has_unverified = any(c.verification_status != VerificationStatus.VERIFIED for c in verified_claims)
        if has_contradiction:
            overall_status = VerificationStatus.CONTRADICTED
            abstention_reason = "contradictory_evidence"
            if self.strict:
                final_answerable = False
        elif has_unverified or not verified_claims or citation_format_error:
            overall_status = VerificationStatus.UNVERIFIED
            abstention_reason = "unverified_claim" if not citation_format_error else "citation_format_error"
            if self.strict:
                final_answerable = False

        # 4. Render final full text strictly from verified claims
        if final_answerable:
            rendered_lines = []
            for vc in verified_claims:
                if vc.verification_status == VerificationStatus.VERIFIED:
                    cit_str = " ".join(f"[{cid}]" for cid in vc.cited_alignment_ids)
                    rendered_lines.append(f"{vc.conclusion} {cit_str}".strip())
            final_text = "\n\n".join(rendered_lines)
            final_summary = rendered_lines[0] if rendered_lines else draft.summary
        else:
            final_text = "**Abstention Notice**: The answer could not be verified against the supported legal corpus."
            final_summary = "Abstained due to verification failure."

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        return GroundedLegalAnswer(
            answer_id=draft.answer_id,
            query=query,
            jurisdiction=draft.jurisdiction,
            answerable=final_answerable,
            verification_status=overall_status,
            summary=final_summary,
            claims=verified_claims,
            full_text=final_text,
            referenced_evidence=[
                ev for ev in evidence
                if (ev.provision.alignment_id or ev.provision.provision_id) in structured_citations
            ],
            abstention_reason=abstention_reason,
            generation_latency_ms=draft.generation_latency_ms,
            verification_latency_ms=latency_ms,
        )
