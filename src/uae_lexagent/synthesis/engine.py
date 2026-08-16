"""Synthesis orchestration engine integrating routing, retrieval, generation, and verification."""

from __future__ import annotations

from typing import Sequence

from ..models import Jurisdiction, LegalEvidence, LegalProvision, LegalQuery
from ..retrieval import RetrievalFilters, RetrieverProtocol
from ..routing import classify_jurisdiction
from .contracts import (
    GeneratorProtocol,
    GroundedLegalAnswer,
    VerificationStatus,
)
from .generators import DeterministicTemplateGenerator
from .policy import evaluate_answerability
from .verifier import CitationVerifier


class LegalSynthesisEngine:
    """End-to-end legal synthesis engine with hard verification gates."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        generator: GeneratorProtocol | None = None,
        verifier: CitationVerifier | None = None,
        provisions: Sequence[LegalProvision] | None = None,
    ) -> None:
        self.retriever = retriever
        self.generator = generator or DeterministicTemplateGenerator()
        valid_ids = {p.alignment_id for p in provisions if p.alignment_id} if provisions else None
        self.verifier = verifier or CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
        self.provisions = list(provisions or [])

    def synthesize(self, query: LegalQuery) -> GroundedLegalAnswer:
        """Full end-to-end statutory synthesis pipeline."""
        # 1. Jurisdiction Routing
        routed_jurisdiction = query.jurisdiction
        if routed_jurisdiction == Jurisdiction.UNKNOWN:
            decision = classify_jurisdiction(query.text)
            routed_jurisdiction = decision.jurisdiction
            query = query.model_copy(update={"jurisdiction": routed_jurisdiction})

        # 2. Corpus-Aware Answerability Check
        answerability = evaluate_answerability(query, provisions=self.provisions)
        if not answerability.answerable:
            draft = self.generator.generate(
                query=query,
                evidence=[],
                jurisdiction=routed_jurisdiction,
                language=query.language,
            )
            draft = draft.model_copy(
                update={
                    "answerable": False,
                    "summary": answerability.explanation,
                    "full_text": f"**Abstention Notice**: {answerability.explanation}",
                    "claims": [],
                    "abstention_reason": answerability.reason,
                }
            )
            return self.verifier.verify(query, draft, [])

        # 3. Retrieve Evidence with Metadata Filters
        evidence = self.retriever.search(
            query,
            top_k=5,
            filters=RetrievalFilters(
                jurisdiction=routed_jurisdiction,
                as_of=query.as_of,
                language=query.language,
                legal_domain="employment",
            ),
        )

        return self.synthesize_from_evidence(query, evidence)

    def synthesize_from_evidence(
        self,
        query: LegalQuery,
        evidence: Sequence[LegalEvidence],
    ) -> GroundedLegalAnswer:
        """Synthesize from caller-provided or retrieved evidence with validation."""
        routed_jurisdiction = query.jurisdiction
        if routed_jurisdiction == Jurisdiction.UNKNOWN:
            decision = classify_jurisdiction(query.text)
            routed_jurisdiction = decision.jurisdiction
            query = query.model_copy(update={"jurisdiction": routed_jurisdiction})

        evaluate = evaluate_answerability(query, evidence, self.provisions)
        if not evaluate.answerable:
            evidence = []

        # Filter and validate caller-provided evidence
        valid_evidence: list[LegalEvidence] = []
        canonical_by_id = {provision.provision_id: provision for provision in self.provisions}
        canonical_alignment_ids = {provision.alignment_id for provision in self.provisions if provision.alignment_id}
        for ev in evidence:
            prov = ev.provision
            if routed_jurisdiction == Jurisdiction.UNKNOWN or prov.jurisdiction != routed_jurisdiction:
                continue
            if prov.legal_domain != "employment":
                continue
            if self.provisions:
                canonical = canonical_by_id.get(prov.provision_id)
                if canonical is None or prov.alignment_id not in canonical_alignment_ids:
                    continue
                if canonical.model_dump(mode="json") != prov.model_dump(mode="json"):
                    continue
            if query.as_of is not None:
                if prov.effective_from is not None and query.as_of < prov.effective_from:
                    continue
                if prov.effective_until is not None and query.as_of > prov.effective_until:
                    continue
            valid_evidence.append(ev)

        # Generate Draft Answer
        if evaluate.answerable and not valid_evidence:
            evaluate = evaluate_answerability(query, [], self.provisions)
        draft = self.generator.generate(
            query=query,
            evidence=valid_evidence,
            jurisdiction=routed_jurisdiction,
            language=query.language,
        )

        if not evaluate.answerable:
            draft = draft.model_copy(
                update={
                    "answerable": False,
                    "summary": evaluate.explanation,
                    "full_text": f"**Abstention Notice**: {evaluate.explanation}",
                    "claims": [],
                    "abstention_reason": evaluate.reason,
                }
            )

        # Verify Claims & Citations via Gate
        return self.verifier.verify(query, draft, valid_evidence)
