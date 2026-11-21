"""Comprehensive test suite for the bounded, auditable legal agent orchestrator (Step 5)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from uae_lexagent.agent import (
    AgentIntent,
    AgentState,
    AgentStatus,
    AgentTraceStep,
    LegalAgentOrchestrator,
)
from uae_lexagent.agent.nodes import (
    abstain_node,
    analyze_node,
    finalize_node,
    reformulate_node,
    retrieval_node,
    synthesis_node,
    tool_node,
    verification_node,
)
from uae_lexagent.agent.router import (
    route_after_analysis,
    route_after_reformulation,
    route_after_verification,
)
from uae_lexagent.evaluation.evaluator import BenchmarkEvaluator
from uae_lexagent.ingestion import load_provisions
from uae_lexagent.models import (
    BenchmarkCase,
    BenchmarkCategory,
    Jurisdiction,
    Language,
    LegalEvidence,
    LegalProvision,
    LegalQuery,
    LegalResponse,
)
from uae_lexagent.retrieval import InMemoryRetriever
from uae_lexagent.synthesis.contracts import (
    DraftLegalClaim,
    GeneratedLegalAnswer,
    GroundedLegalAnswer,
    VerificationStatus,
    VerifiedLegalClaim,
)
from uae_lexagent.synthesis.generators import DeterministicTemplateGenerator
from uae_lexagent.synthesis.verifier import CitationVerifier
from uae_lexagent.tools import GratuityInput

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def canonical_provisions() -> list[LegalProvision]:
    fdl = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    cab = load_provisions(
        DATA_DIR / "official_sources" / "cabinet_resolution_1_2022_provisions.json"
    )
    return fdl + cab


@pytest.fixture(scope="module")
def retriever(canonical_provisions: list[LegalProvision]) -> InMemoryRetriever:
    return InMemoryRetriever(canonical_provisions)


@pytest.fixture(scope="module")
def verifier(canonical_provisions: list[LegalProvision]) -> CitationVerifier:
    alignments = {p.alignment_id for p in canonical_provisions if p.alignment_id}
    return CitationVerifier(valid_corpus_alignment_ids=alignments, strict=True)


# 1. Agent state initialization
def test_agent_state_initialization() -> None:
    query = LegalQuery(
        query_id="Q_INIT_001",
        text="What is the probation period in UAE Labour Law?",
        language=Language.EN,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
    )
    state = AgentState(query=query, max_attempts=2, max_steps=15, timeout_seconds=5.0)

    assert state.query.query_id == "Q_INIT_001"
    assert state.max_attempts == 2
    assert state.max_steps == 15
    assert state.timeout_seconds == 5.0
    assert state.current_status == AgentStatus.RUNNING
    assert len(state.trace) == 0
    assert len(state.tool_calls) == 0
    assert state.has_step_budget()
    assert not state.is_timed_out()


# 2. Trace creation and append-only behavior
def test_trace_creation_and_append_only() -> None:
    query = LegalQuery(query_id="Q_TRACE_001", text="Sample question")
    state = AgentState(query=query)

    state.add_trace(
        node_name="analyze_node",
        action="classify",
        status="success",
        rationale="Jurisdiction classified as UAE onshore",
        duration_ms=1.234,
        input_summary="Sample question",
        output_summary="Classified",
        metadata={"jurisdiction": "uae_onshore"},
    )

    assert len(state.trace) == 1
    step = state.trace[0]
    assert isinstance(step, AgentTraceStep)
    assert step.step_index == 0
    assert step.node_name == "analyze_node"
    assert step.status == "success"
    assert step.duration_ms == 1.234
    assert step.metadata["jurisdiction"] == "uae_onshore"

    # Append second step
    state.add_trace(
        node_name="retrieval_node",
        action="search",
        status="success",
        rationale="Retrieved 2 items",
        duration_ms=2.5,
    )
    assert len(state.trace) == 2
    assert state.trace[1].step_index == 1


# 3. Direct factual routing
def test_direct_factual_routing(canonical_provisions: list[LegalProvision]) -> None:
    query = LegalQuery(
        query_id="Q_DIRECT_001",
        text="What is the maximum duration of probation under UAE Labour Law?",
        language=Language.EN,
    )
    state = AgentState(query=query)
    state = analyze_node(state, canonical_provisions)

    assert state.intent == AgentIntent.DIRECT_FACTUAL
    assert state.jurisdiction == Jurisdiction.UAE_ONSHORE
    assert len(state.sub_queries) == 1
    assert route_after_analysis(state) == "retrieve"


# 4. Multi-hop decomposition
def test_multi_hop_decomposition(canonical_provisions: list[LegalProvision]) -> None:
    query = LegalQuery(
        query_id="Q_MH_001",
        text="Under UAE Labour Law, during probation can I resign to join a competitor and is the non-compete enforceable?",
        language=Language.EN,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
    )
    state = AgentState(query=query)
    state = analyze_node(state, canonical_provisions)

    assert state.intent == AgentIntent.MULTI_HOP
    assert len(state.sub_queries) >= 2
    # Verify sub-queries address both probation and non-compete
    has_probation = any("probation" in sq.lower() for sq in state.sub_queries)
    has_non_compete = any("non-compete" in sq.lower() for sq in state.sub_queries)
    assert has_probation
    assert has_non_compete
    assert route_after_analysis(state) == "retrieve"


# 5. Arabic and mixed-language routing
def test_arabic_and_mixed_language_routing(
    canonical_provisions: list[LegalProvision],
) -> None:
    # Arabic query
    ar_query = LegalQuery(
        query_id="Q_AR_001", text="ما هي أقصى فترة تجربة في قانون العمل الإماراتي؟"
    )
    ar_state = AgentState(query=ar_query)
    ar_state = analyze_node(ar_state, canonical_provisions)
    assert ar_state.language == Language.AR
    assert ar_state.output_language == "ar"

    # Mixed code-switched query
    mixed_query = LegalQuery(
        query_id="Q_MIX_001",
        text="كم notice period المطلوبة during probation في الإمارات؟",
    )
    mixed_state = AgentState(query=mixed_query)
    mixed_state = analyze_node(mixed_state, canonical_provisions)
    assert mixed_state.language == Language.MIXED
    assert mixed_state.output_language in ("en", "ar")


# 6. Unknown jurisdiction abstention
def test_unknown_jurisdiction_abstention(
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(
        query_id="Q_UNK_JURIS",
        text="Under Singapore employment law, what is the statutory notice period?",
    )
    state = AgentState(query=query)
    state = analyze_node(state, canonical_provisions)

    assert state.intent == AgentIntent.ABSTENTION
    assert state.current_status == AgentStatus.ABSTAINED
    assert route_after_analysis(state) == "abstain"


# 7. DIFC and ADGM abstention
def test_difc_and_adgm_abstention(canonical_provisions: list[LegalProvision]) -> None:
    # DIFC
    difc_query = LegalQuery(
        query_id="Q_DIFC",
        text="Under DIFC Employment Law, what are the end of service benefits?",
    )
    difc_state = analyze_node(AgentState(query=difc_query), canonical_provisions)
    assert difc_state.current_status == AgentStatus.ABSTAINED
    assert route_after_analysis(difc_state) == "abstain"

    # ADGM
    adgm_query = LegalQuery(
        query_id="Q_ADGM",
        text="What is the probation period in ADGM financial free zone?",
    )
    adgm_state = analyze_node(AgentState(query=adgm_query), canonical_provisions)
    assert adgm_state.current_status == AgentStatus.ABSTAINED
    assert route_after_analysis(adgm_state) == "abstain"


# 8. Missing article abstention
def test_missing_article_abstention(canonical_provisions: list[LegalProvision]) -> None:
    query = LegalQuery(
        query_id="Q_NON_EXISTENT",
        text="Under Article 999 of Federal Decree-Law No. 33 of 2021, what are space worker rights?",
    )
    state = AgentState(query=query)
    state = analyze_node(state, canonical_provisions)

    assert state.current_status == AgentStatus.ABSTAINED
    assert state.abstention_reason == "unknown_article"
    assert route_after_analysis(state) == "abstain"


# 9. Historical corpus-gap abstention
def test_historical_corpus_gap_abstention(
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(
        query_id="Q_HISTORICAL",
        text="What was the maternity leave in Dubai in 1995 under Law No. 8 of 1980?",
        as_of=date(1995, 6, 1),
    )
    state = AgentState(query=query)
    state = analyze_node(state, canonical_provisions)

    assert state.current_status == AgentStatus.ABSTAINED
    assert state.abstention_reason in (
        "historical_corpus_gap",
        "out_of_scope_worker_category",
    )
    assert route_after_analysis(state) == "abstain"


# 10. Hard retrieval filters
def test_hard_retrieval_filters(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(
        query_id="Q_FILTER_001",
        text="What is the probation period under Article 9?",
        language=Language.EN,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
    )
    state = AgentState(query=query, sub_queries=[query.text])
    state = retrieval_node(state, retriever, canonical_provisions)

    assert len(state.retrieved_evidence) > 0
    for ev in state.retrieved_evidence:
        assert ev.provision.jurisdiction == Jurisdiction.UAE_ONSHORE


# 11. Evidence deduplication by alignment ID
def test_evidence_deduplication_by_alignment_id(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_DEDUP", text="Article 9 probation duration notice")
    # Sub-queries that target the same article
    sub_q1 = "Under UAE Labour Law, what is probation under Article 9?"
    sub_q2 = "Article 9 clause 1 employer probation notice"
    state = AgentState(query=query, sub_queries=[sub_q1, sub_q2])

    state = retrieval_node(state, retriever, canonical_provisions)

    # Check alignment IDs are distinct
    alignment_ids = [ev.provision.alignment_id for ev in state.retrieved_evidence]
    assert len(alignment_ids) == len(set(alignment_ids))


# 12. Gratuity tool input validation
def test_gratuity_tool_input_validation() -> None:
    # Valid input
    valid_in = GratuityInput(basic_salary=Decimal("15000"), service_years=Decimal("3.5"))
    assert valid_in.basic_salary == Decimal("15000")
    assert valid_in.service_years == Decimal("3.5")

    # Negative basic salary rejected
    with pytest.raises(ValidationError):
        GratuityInput(basic_salary=Decimal("-5000"), service_years=Decimal("2"))

    # Negative service years rejected
    with pytest.raises(ValidationError):
        GratuityInput(basic_salary=Decimal("5000"), service_years=Decimal("-1"))


# 13. Gratuity tool legal-basis gating
def test_gratuity_tool_legal_basis_gating() -> None:
    query = LegalQuery(
        query_id="Q_GATE_001",
        text="Calculate gratuity for basic salary AED 12000 and 4.0 years of service.",
    )
    # State without the required legal basis "FDL_33_2021_A51_C2"
    state_without_basis = AgentState(query=query)
    state_without_basis = tool_node(state_without_basis)
    assert len(state_without_basis.tool_calls) == 0
    assert any(t.status == "skipped" for t in state_without_basis.trace)

    # State with required legal basis
    state_with_basis = AgentState(query=query)
    state_with_basis.deduplicated_alignment_ids.add("FDL_33_2021_A51_C2")
    state_with_basis = tool_node(state_with_basis)
    assert len(state_with_basis.tool_calls) == 1
    assert state_with_basis.tool_calls[0].valid_schema is True
    assert state_with_basis.tool_calls[0].statutory_basis == "FDL_33_2021_A51_C2"


# 14. Tool result binding into synthesis
def test_tool_result_binding_into_synthesis(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(
        query_id="Q_BIND_001",
        text="Calculate gratuity for basic salary AED 10000 and 2.0 years of service under UAE Labour Law.",
    )
    gen = DeterministicTemplateGenerator()
    state = AgentState(
        query=query,
        sub_queries=["Under UAE Labour Law Article 51 gratuity calculation"],
    )
    state = retrieval_node(state, retriever, canonical_provisions)
    state = tool_node(state)
    assert len(state.tool_calls) == 1

    state = synthesis_node(state, gen)
    assert state.draft_answer is not None
    # Verify calculated amount is present in claims
    has_calc_claim = any(c.claim_key == "gratuity_calculation" for c in state.draft_answer.claims)
    assert has_calc_claim
    assert state.draft_answer.tool_calls == state.tool_calls


# 15. Invalid tool-result rejection
def test_invalid_tool_result_rejection(
    verifier: CitationVerifier,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_INV_TOOL", text="Calculate gratuity for AED 10000 and 1.0 years")
    state = AgentState(query=query)
    state.deduplicated_alignment_ids.add("FDL_33_2021_A51_C2")

    # Tampered / mismatched tool result
    state.tool_results = {"total_gratuity": 99999.0}
    # Draft claim has a different number
    draft_claim = DraftLegalClaim(
        claim_id="clm_calc",
        claim_key="gratuity_calculation",
        conclusion="Under Article 51, the statutory end-of-service gratuity is AED 7000.00.",
        cited_alignment_ids=["FDL_33_2021_A51_C2"],
    )
    prov = next(p for p in canonical_provisions if p.alignment_id == "FDL_33_2021_A51_C2")
    evidence = [LegalEvidence(evidence_id="ev1", provision=prov, retrieval_rank=1)]
    state.retrieved_evidence = evidence
    state.draft_answer = GeneratedLegalAnswer(
        answer_id="ans1",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Gratuity",
        claims=[draft_claim],
        full_text="Under Article 51, the statutory end-of-service gratuity is AED 7000.00.",
        referenced_evidence=evidence,
    )

    state = verification_node(state, verifier)
    assert state.verified_answer is not None
    assert state.verified_answer.verification_status == VerificationStatus.CONTRADICTED


# 16. Valid citation verification
def test_valid_citation_verification(
    retriever: InMemoryRetriever,
    verifier: CitationVerifier,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(
        query_id="Q_VAL_CIT",
        text="What is the maximum probation period under UAE Labour Law?",
        language=Language.EN,
    )
    gen = DeterministicTemplateGenerator()
    state = AgentState(query=query, sub_queries=[query.text])
    state = retrieval_node(state, retriever, canonical_provisions)
    state = synthesis_node(state, gen)
    state = verification_node(state, verifier)

    assert state.verified_answer is not None
    assert state.verified_answer.verification_status == VerificationStatus.VERIFIED
    assert len(state.verified_answer.claims) > 0
    for vc in state.verified_answer.claims:
        assert vc.verification_status == VerificationStatus.VERIFIED


# 17. Mixed verified and unverified claims causing strict abstention
def test_mixed_claims_causing_strict_abstention(
    verifier: CitationVerifier,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_MIXED_CLAIMS", text="Mixed claim query")
    prov = next(p for p in canonical_provisions if p.alignment_id == "FDL_33_2021_A9_C1")
    evidence = [LegalEvidence(evidence_id="ev1", provision=prov, retrieval_rank=1)]

    # 1 valid claim, 1 unverified claim (citing a non-existent alignment ID)
    claim1 = DraftLegalClaim(
        claim_id="c1",
        claim_key="probation_maximum_duration",
        conclusion="Probation shall not exceed 6 months under Article 9.",
        cited_alignment_ids=["FDL_33_2021_A9_C1"],
    )
    claim2 = DraftLegalClaim(
        claim_id="c2",
        claim_key="fabricated_right",
        conclusion="Workers receive 50 days bonus on probation under Article 999.",
        cited_alignment_ids=["FDL_33_2021_A999_C1"],
    )

    draft = GeneratedLegalAnswer(
        answer_id="ans_mixed",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Mixed claims",
        claims=[claim1, claim2],
        full_text="Probation shall not exceed 6 months. Workers receive 50 days bonus.",
        referenced_evidence=evidence,
    )

    state = AgentState(query=query, draft_answer=draft, retrieved_evidence=evidence)
    state = verification_node(state, verifier)

    # In strict mode, presence of an unverified claim prevents full release
    assert state.verified_answer is not None
    assert state.verified_answer.answerable is False


# 18. Contradiction causing immediate abstention
def test_contradiction_causing_immediate_abstention(
    verifier: CitationVerifier,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_CONTRADICT", text="Contradictory query")
    prov = next(p for p in canonical_provisions if p.alignment_id == "FDL_33_2021_A9_C1")
    evidence = [LegalEvidence(evidence_id="ev1", provision=prov, retrieval_rank=1)]

    # Contradicts statutory 6 months probation
    contradictory_claim = DraftLegalClaim(
        claim_id="c_contra",
        claim_key="probation_max_months",
        conclusion="Probation period can extend up to 2 years under Article 9.",
        cited_alignment_ids=["FDL_33_2021_A9_C1"],
    )
    draft = GeneratedLegalAnswer(
        answer_id="ans_contra",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Contradiction",
        claims=[contradictory_claim],
        full_text="Probation period can extend up to 2 years under Article 9.",
        referenced_evidence=evidence,
    )

    state = AgentState(query=query, draft_answer=draft, retrieved_evidence=evidence)
    state = verification_node(state, verifier)

    assert state.current_status == AgentStatus.ABSTAINED
    assert route_after_verification(state) == "abstain"


# 19. Retry for recoverable missing evidence
def test_retry_for_recoverable_missing_evidence(
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_RETRY_REC", text="Recoverable query")
    # Claim cites Article 10 which is in the canonical corpus, but wasn't retrieved initially
    unverified_claim = VerifiedLegalClaim(
        claim_id="c_unv",
        claim_key="non_compete_limits",
        conclusion="A non-compete clause cannot exceed 2 years under Article 10.",
        cited_alignment_ids=["FDL_33_2021_A10_C1"],
        verification_status=VerificationStatus.UNVERIFIED,
        verification_reasons=["Citation not present in retrieved evidence."],
    )
    grounded = GroundedLegalAnswer(
        answer_id="ans_unv",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=False,
        verification_status=VerificationStatus.UNVERIFIED,
        summary="Unverified",
        claims=[unverified_claim],
    )

    state = AgentState(
        query=query,
        verified_answer=grounded,
        verification_attempts=1,
        max_attempts=2,
    )

    state = reformulate_node(state, canonical_provisions)
    assert state.current_status == AgentStatus.RETRIEVING
    assert route_after_reformulation(state) == "retrieve"
    assert len(state.sub_queries) > 0
    assert "FDL_33_2021_A10_C1" in state.visited_reformulation_keys


# 20. No retry for contradictions
def test_no_retry_for_contradictions() -> None:
    query = LegalQuery(query_id="Q_NO_RETRY", text="Contradictory query")
    state = AgentState(
        query=query,
        current_status=AgentStatus.ABSTAINED,
        abstention_reason="contradictory_evidence",
    )
    action = route_after_verification(state)
    assert action == "abstain"


# 21. Retry budget enforcement
def test_retry_budget_enforcement(canonical_provisions: list[LegalProvision]) -> None:
    query = LegalQuery(query_id="Q_EXHAUST", text="Budget query")
    state = AgentState(
        query=query,
        verification_attempts=2,
        max_attempts=2,
        current_status=AgentStatus.ABSTAINED,
    )
    state = reformulate_node(state, canonical_provisions)
    assert state.current_status == AgentStatus.ABSTAINED
    assert state.abstention_reason == "exhausted_budget"


# 22. Maximum graph-step enforcement
def test_maximum_graph_step_enforcement(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_MAX_STEPS", text="Sample query")
    state = AgentState(query=query, max_steps=2, max_execution_steps=2)

    # Exhaust execution budget
    state.execution_steps = 2
    state.step_count = 2

    assert not state.has_step_budget()
    state = retrieval_node(state, retriever, canonical_provisions)
    assert state.current_status == AgentStatus.ABSTAINED
    assert state.abstention_reason == "exhausted_budget"


# 23. No duplicate reformulations
def test_no_duplicate_reformulations(
    canonical_provisions: list[LegalProvision],
) -> None:
    query = LegalQuery(query_id="Q_NO_DUP", text="Duplicate reformulation query")
    unverified_claim = VerifiedLegalClaim(
        claim_id="c_unv",
        claim_key="non_compete_limits",
        conclusion="Non-compete claim",
        cited_alignment_ids=["FDL_33_2021_A10_C1"],
        verification_status=VerificationStatus.UNVERIFIED,
    )
    grounded = GroundedLegalAnswer(
        answer_id="ans_dup",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=False,
        verification_status=VerificationStatus.UNVERIFIED,
        summary="Unv",
        claims=[unverified_claim],
    )

    state = AgentState(
        query=query,
        verified_answer=grounded,
        verification_attempts=1,
        max_attempts=3,
        visited_reformulation_keys={"FDL_33_2021_A10_C1"},  # already visited!
    )

    state = reformulate_node(state, canonical_provisions)
    # Cannot reformulate duplicate key, so safely transitions to abstention
    assert state.current_status == AgentStatus.ABSTAINED


# 24. Safe abstention response content
def test_safe_abstention_response_content(verifier: CitationVerifier) -> None:
    query = LegalQuery(query_id="Q_SAFE_ABS", text="Out of scope query")
    state = AgentState(query=query, abstention_reason="unsupported_jurisdiction")
    state = abstain_node(state, verifier)
    response = finalize_node(state)

    assert isinstance(response, LegalResponse)
    assert response.answerable is False
    assert "**Abstention Notice**" in response.answer
    assert len(response.claims) == 0
    assert response.verification.passed is False


# 25. Deterministic ID reproducibility
def test_deterministic_id_reproducibility() -> None:
    gen = DeterministicTemplateGenerator()
    q1 = LegalQuery(query_id="Q_REP_1", text="What is probation period in UAE Labour Law?")
    q2 = LegalQuery(query_id="Q_REP_1", text="What is probation period in UAE Labour Law?")

    ans1 = gen.generate(q1, [])
    ans2 = gen.generate(q2, [])
    assert ans1.answer_id == ans2.answer_id


# 26. Deterministic runtime end-to-end execution
def test_deterministic_runtime_end_to_end(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        runtime="deterministic",
    )
    query = "Under UAE Labour Law, what is the maximum probation period?"
    response = orchestrator.run(query)

    assert isinstance(response, LegalResponse)
    assert response.answerable is True
    assert response.jurisdiction == Jurisdiction.UAE_ONSHORE
    assert len(response.claims) > 0
    assert response.verification.passed is True
    assert len(response.trace) > 0


# 27. LangGraph runtime equivalence using pytest.importorskip
def test_langgraph_runtime_equivalence(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    pytest.importorskip("langgraph")

    orch_det = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        runtime="deterministic",
    )
    orch_lg = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        runtime="langgraph",
    )

    query = "Under UAE Labour Law, what is the maximum duration of the probation period?"
    resp_det = orch_det.run(query)
    resp_lg = orch_lg.run(query)

    # Semantic equivalence check
    assert resp_det.answerable == resp_lg.answerable
    assert resp_det.jurisdiction == resp_lg.jurisdiction
    assert resp_det.verification.passed == resp_lg.verification.passed
    assert len(resp_det.claims) == len(resp_lg.claims)
    assert {c.text for c in resp_det.claims} == {c.text for c in resp_lg.claims}


# 28. LegalResponse schema validation
def test_legal_response_schema_validation(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
    )
    resp = orchestrator.run("What is the probation period in UAE Labour Law?")

    # Pydantic serialization round-trip
    data = resp.model_dump()
    reconstructed = LegalResponse.model_validate(data)
    assert reconstructed.query_id == resp.query_id
    assert reconstructed.answerable == resp.answerable
    assert len(reconstructed.trace) == len(resp.trace)


# 29. Multi-hop retrieval of Article 9 and Article 10
def test_multi_hop_retrieval_article_9_and_10(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
    )
    query = (
        "Under UAE Labour Law, during probation if a worker resigns to join another employer in the UAE, "
        "what notice is required and what non-compete rules apply?"
    )
    response = orchestrator.run(query)

    assert response.answerable is True
    retrieved_alignments = {ev.provision.alignment_id for ev in response.evidence}
    # Check that provisions from both Article 9 and Article 10 were retrieved
    has_art9 = any("A9" in str(a) for a in retrieved_alignments)
    has_art10 = any("A10" in str(a) for a in retrieved_alignments)
    assert has_art9, f"Article 9 missing from retrieved alignments: {retrieved_alignments}"
    assert has_art10, f"Article 10 missing from retrieved alignments: {retrieved_alignments}"


# 30. Benchmark evaluator agent mode
def test_benchmark_evaluator_agent_mode(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    alignments = {p.alignment_id for p in canonical_provisions if p.alignment_id}
    evaluator = BenchmarkEvaluator(
        retriever=retriever,
        valid_alignment_ids=alignments,
        provisions=canonical_provisions,
        mode="agent",
        runtime="deterministic",
    )

    case = BenchmarkCase(
        case_id="TEST_AGENT_001",
        query="Under UAE Labour Law, what is the statutory probation period?",
        language=Language.EN,
        category=BenchmarkCategory.DIRECT_FACTUAL,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_provisions=["FDL_33_2021_A9_C1"],
        expected_corpus_version="2021-as-amended-2024",
    )

    pred = evaluator.predict_case(case)
    assert pred.case_id == "TEST_AGENT_001"
    assert pred.answerable is True
    assert pred.jurisdiction == Jurisdiction.UAE_ONSHORE
    assert len(pred.claims) > 0


# 31. P50/P95 latency collection
def test_latency_collection(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
    )
    resp = orchestrator.run("What is the probation period?")
    assert len(resp.trace) > 0
    for step in resp.trace:
        assert step.duration_ms >= 0.0
        assert isinstance(step.duration_ms, float)


# 32. Provider failure handling
def test_provider_failure_handling(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    class FailingGenerator:
        def generate(self, *args, **kwargs):
            raise RuntimeError("LLM provider timeout / service unavailable")

    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        generator=FailingGenerator(),  # type: ignore
    )

    # When generator fails, agent safely abstains with provider_failure reason
    resp = orchestrator.run("Under UAE Labour Law, what is the probation period?")
    assert resp.answerable is False
    assert resp.fallback_reason == "provider_failure"
    assert "**Abstention Notice**" in resp.answer


def test_agent_calculation_is_verified_and_idempotent(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(retriever=retriever, provisions=canonical_provisions)
    query = "Under UAE Labour Law, calculate gratuity for basic salary AED 10000 and 2.0 years of service."
    first = orchestrator.run(query)
    second = orchestrator.run(query)

    assert first.answerable is True
    assert first.verification.passed is True
    assert first.tool_calls[0].result["total_gratuity"] == 14000.0
    assert len(first.tool_calls) == 1
    assert first.query_id == second.query_id
    assert first.answer == second.answer


def test_agent_preserves_bilingual_multihop_coverage(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    orchestrator = LegalAgentOrchestrator(retriever=retriever, provisions=canonical_provisions)
    response = orchestrator.run(
        "بموجب قانون العمل الإماراتي، خلال فترة التجربة إذا استقال العامل للانضمام إلى صاحب عمل آخر، ما مدة الإخطار وما قواعد عدم المنافسة؟"
    )
    alignments = {e.provision.alignment_id for e in response.evidence}
    assert response.answerable is True
    assert "FDL_33_2021_A9_C3" in alignments
    assert "FDL_33_2021_A10_C1" in alignments
    assert {claim.claim_key for claim in response.claims} == {
        "probation_employee_transfer_notice_months",
        "non_compete_max_years",
    }


def test_agent_trace_respects_step_budget(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    response = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        max_steps=1,
    ).run("Under UAE Labour Law, what is the maximum probation period?")
    assert response.answerable is False
    assert response.fallback_reason == "exhausted_budget"
    # Terminal abstention and finalization steps are preserved in trace even when step budget is 1
    assert any(s.node_name == "abstain_node" for s in response.trace)
    assert any(s.node_name == "finalize_node" for s in response.trace)


def test_tool_calculation_contradiction_overwrites_full_text(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    """When a calculated claim's number contradicts validated tool output, full_text must be overwritten with an abstention notice."""
    verifier = CitationVerifier(
        valid_corpus_alignment_ids={p.alignment_id for p in canonical_provisions if p.alignment_id}
    )
    query = LegalQuery(
        query_id="Q_TOOL_CONTRA",
        text="Under UAE Labour Law, calculate gratuity for 10000 salary and 3 years",
    )
    prov = next(p for p in canonical_provisions if p.alignment_id == "FDL_33_2021_A51_C2")
    evidence = [LegalEvidence(evidence_id="ev_a51", provision=prov, retrieval_rank=1)]

    # Tool calculated 21000, but draft claims 99999
    draft_claim = DraftLegalClaim(
        claim_id="c_gratuity_bad",
        claim_key="gratuity_calculation",
        conclusion="Under Article 51, the statutory end-of-service gratuity is AED 99999.00 for basic salary AED 10000.00 and 3.0 years of service.",
        cited_alignment_ids=["FDL_33_2021_A51_C2"],
        tool_derived=True,
    )
    draft = GeneratedLegalAnswer(
        answer_id="ans_contra_tool",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Gratuity calculation",
        claims=[draft_claim],
        full_text=draft_claim.conclusion,
        referenced_evidence=evidence,
    )

    state = AgentState(
        query=query,
        draft_answer=draft,
        retrieved_evidence=evidence,
        tool_results={
            "basic_salary": 10000.0,
            "service_years": 3.0,
            "total_gratuity": 21000.0,
        },
    )
    state = verification_node(state, verifier)

    assert state.verified_answer is not None
    assert state.verified_answer.answerable is False
    assert state.verified_answer.verification_status == VerificationStatus.CONTRADICTED
    assert "**Abstention Notice**" in state.verified_answer.full_text
    assert "99999" not in state.verified_answer.full_text


def test_unverified_answer_never_leaks_unsupported_text() -> None:
    """Finalize node must never leak draft text if answer is unverified or contradicted."""
    query = LegalQuery(query_id="Q_LEAK_CHECK", text="Sample query")
    unv_answer = GroundedLegalAnswer(
        answer_id="ans_unv",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=False,
        verification_status=VerificationStatus.UNVERIFIED,
        summary="Unverified draft summary",
        claims=[
            VerifiedLegalClaim(
                claim_id="c_unv",
                claim_key="probation_max_months",
                conclusion="Unverified assertion of legal right.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
                verification_status=VerificationStatus.UNVERIFIED,
                verification_reasons=["Citation not in retrieved evidence."],
                support_score=0.0,
            )
        ],
        full_text="Unverified assertion of legal right. [FDL_33_2021_A9_C1]",
        referenced_evidence=[],
        abstention_reason="unverified_claim",
    )

    state = AgentState(query=query, verified_answer=unv_answer)
    response = finalize_node(state)

    assert response.answerable is False
    assert response.answer.startswith("**Abstention Notice**")
    assert "Unverified assertion of legal right" not in response.answer
    assert all(c.supported is False for c in response.claims)


def test_error_sanitization_masks_internal_exception_details(
    retriever: InMemoryRetriever,
    canonical_provisions: list[LegalProvision],
) -> None:
    """Exception strings containing sensitive internal details must be sanitized to error type names."""

    class LeakyGenerator:
        def generate(self, *args, **kwargs):
            raise ConnectionError(
                "Host https://admin:SECRET_TOKEN@internal.cluster.local:8080 timed out"
            )

    orchestrator = LegalAgentOrchestrator(
        retriever=retriever,
        provisions=canonical_provisions,
        generator=LeakyGenerator(),  # type: ignore
    )

    resp = orchestrator.run("Under UAE Labour Law, what is the probation period?")
    assert resp.answerable is False
    assert resp.fallback_reason == "provider_failure"

    # Confirm secrets/hostnames are never leaked into response issues or trace
    for issue in resp.verification.issues:
        assert "SECRET_TOKEN" not in issue
        assert "internal.cluster.local" not in issue
    for step in resp.trace:
        assert "SECRET_TOKEN" not in step.rationale
        assert "internal.cluster.local" not in step.rationale


def test_expanded_alignment_keys_verify_article_53_and_66(
    canonical_provisions: list[LegalProvision],
) -> None:
    """Articles 53 and 66 must have valid alignment keys in CitationVerifier."""
    valid_ids = {p.alignment_id for p in canonical_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    query = LegalQuery(
        query_id="Q_A53", text="Within how many days must employer pay entitlements?"
    )

    prov_53 = next(p for p in canonical_provisions if p.alignment_id == "FDL_33_2021_A53_C1")
    ev_53 = [LegalEvidence(evidence_id="ev_53", provision=prov_53, retrieval_rank=1)]

    draft = GeneratedLegalAnswer(
        answer_id="ans_53",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Entitlement timeline",
        claims=[
            DraftLegalClaim(
                claim_id="c_53",
                claim_key="final_dues_payment_days",
                conclusion="Employer must pay all entitlements within 14 days from contract termination.",
                cited_alignment_ids=["FDL_33_2021_A53_C1"],
            )
        ],
        full_text="Employer must pay all entitlements within 14 days from contract termination. [FDL_33_2021_A53_C1]",
        referenced_evidence=ev_53,
    )

    grounded = verifier.verify(query, draft, ev_53)
    assert grounded.answerable is True
    assert grounded.verification_status == VerificationStatus.VERIFIED
    assert len(grounded.claims) == 1
    assert grounded.claims[0].verification_status == VerificationStatus.VERIFIED
