"""Strict legal safety, jurisdiction, temporal, and adversarial regression tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from uae_lexagent.agent.graph import DeterministicAgentGraph
from uae_lexagent.agent.nodes import finalize_node
from uae_lexagent.agent.state import AgentState
from uae_lexagent.ingestion import load_provisions
from uae_lexagent.models import (
    Jurisdiction,
    LegalEvidence,
    LegalQuery,
)
from uae_lexagent.retrieval import InMemoryRetriever
from uae_lexagent.synthesis.contracts import (
    DraftLegalClaim,
    GeneratedLegalAnswer,
    VerificationStatus,
)
from uae_lexagent.synthesis.generators import DeterministicTemplateGenerator
from uae_lexagent.synthesis.verifier import CitationVerifier
from uae_lexagent.tools import calculate_gratuity

DATA_DIR = Path(__file__).parent.parent / "data" / "official_sources"
FDL_PATH = DATA_DIR / "fdl_33_2021_provisions.json"
CABINET_PATH = DATA_DIR / "cabinet_resolution_1_2022_provisions.json"


@pytest.fixture
def corpus_provisions():
    fdl = load_provisions(FDL_PATH)
    cab = load_provisions(CABINET_PATH)
    return fdl + cab


@pytest.fixture
def agent_graph(corpus_provisions):
    retriever = InMemoryRetriever(corpus_provisions)
    generator = DeterministicTemplateGenerator()
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    return DeterministicAgentGraph(
        retriever=retriever,
        generator=generator,
        verifier=verifier,
        provisions=corpus_provisions,
    )


@pytest.mark.legal_safety
def test_free_zone_jurisdiction_rejection_fail_closed(agent_graph):
    """DIFC and ADGM queries must strictly abstain and never produce an answerable response."""
    difc_query = LegalQuery(
        query_id="safety-difc-01",
        text="Under DIFC employment law, what is the maximum probation period?",
        jurisdiction=Jurisdiction.DIFC,
    )
    res = agent_graph.invoke(AgentState(query=difc_query))
    assert res.answerable is False
    assert res.confidence == 0.0
    assert res.evidence == []
    assert res.verification.passed is False
    assert res.verification.jurisdiction_valid is False
    assert "**Abstention Notice**" in res.answer


@pytest.mark.legal_safety
def test_temporal_gap_rejection_fail_closed(corpus_provisions):
    """Historical queries before 2022 citing 2021 law (in effect 2022-02-02) must fail verification."""
    pre_2022_query = LegalQuery(
        query_id="safety-temporal-01",
        text="In 2020, what was the probation notice period?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2020, 1, 1),
    )
    # Draft cites Article 9(1) of FDL 33/2021 which took effect on 2022-02-02
    draft = GeneratedLegalAnswer(
        answer_id="ans-temporal-test",
        query=pre_2022_query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Probation notice is 14 days.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_employer_notice_days",
                conclusion="The employer must give 14 days written notice.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="The employer must give 14 days written notice. [FDL_33_2021_A9_C1]",
        tool_calls=[],
    )
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    evidence = [
        LegalEvidence(
            evidence_id=f"ev-{p.provision_id}",
            provision=p,
            retrieval_rank=1,
        )
        for p in corpus_provisions
        if p.alignment_id == "FDL_33_2021_A9_C1"
    ]
    grounded = verifier.verify(pre_2022_query, draft, evidence)
    assert grounded.answerable is False
    assert grounded.verification_status == VerificationStatus.CONTRADICTED

    state = AgentState(
        query=pre_2022_query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        verified_answer=grounded,
    )
    resp = finalize_node(state)
    assert resp.answerable is False
    assert resp.confidence == 0.0
    assert resp.verification.passed is False
    assert resp.verification.temporal_valid is False
    assert "**Abstention Notice**" in resp.answer


@pytest.mark.legal_safety
def test_fabricated_citation_rejection(corpus_provisions):
    """Citations not in official corpus must fail verification and lead to fail-closed response."""
    query = LegalQuery(
        query_id="safety-fab-01",
        text="What is the probation period?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    draft = GeneratedLegalAnswer(
        answer_id="ans-fab-test",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Probation is 6 months.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_max_months",
                conclusion="Probation shall not exceed 6 months.",
                cited_alignment_ids=["FABRICATED_LAW_ARTICLE_999"],
            )
        ],
        full_text="Probation shall not exceed 6 months. [FABRICATED_LAW_ARTICLE_999]",
        tool_calls=[],
    )
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    grounded = verifier.verify(query, draft, [])
    assert grounded.answerable is False
    assert grounded.verification_status == VerificationStatus.UNVERIFIED

    state = AgentState(
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        verified_answer=grounded,
    )
    resp = finalize_node(state)
    assert resp.answerable is False
    assert resp.verification.passed is False
    assert resp.verification.citation_valid is False


@pytest.mark.legal_safety
def test_reversed_statutory_obligation_detection(corpus_provisions):
    """Claiming that worker must give notice under employer notice clause must be marked CONTRADICTED."""
    query = LegalQuery(
        query_id="safety-reversed-01",
        text="During probation, who must give notice?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    # Under Article 9 Clause 1, the obligation is strictly on the EMPLOYER to give 14 days notice
    draft = GeneratedLegalAnswer(
        answer_id="ans-reversed-test",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Worker must give notice.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_employer_notice_days",
                conclusion="The worker must give 14 days notice to terminate during probation.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="The worker must give 14 days notice to terminate during probation. [FDL_33_2021_A9_C1]",
        tool_calls=[],
    )
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    evidence = [
        LegalEvidence(
            evidence_id=f"ev-{p.provision_id}",
            provision=p,
            retrieval_rank=1,
        )
        for p in corpus_provisions
        if p.alignment_id == "FDL_33_2021_A9_C1"
    ]
    grounded = verifier.verify(query, draft, evidence)
    assert grounded.answerable is False
    assert grounded.verification_status == VerificationStatus.CONTRADICTED
    assert any(
        "reverses statutory obligation" in r for r in grounded.claims[0].verification_reasons
    )


@pytest.mark.legal_safety
def test_contradicted_numeric_quantity_detection(corpus_provisions):
    """Claiming incorrect notice days (e.g. 30 days instead of 14 days) must be CONTRADICTED."""
    query = LegalQuery(
        query_id="safety-num-01",
        text="What is the employer's notice during probation?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    draft = GeneratedLegalAnswer(
        answer_id="ans-num-test",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Notice is 30 days.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_employer_notice_days",
                conclusion="The employer must give 30 days notice to the worker.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="The employer must give 30 days notice to the worker. [FDL_33_2021_A9_C1]",
        tool_calls=[],
    )
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    evidence = [
        LegalEvidence(
            evidence_id=f"ev-{p.provision_id}",
            provision=p,
            retrieval_rank=1,
        )
        for p in corpus_provisions
        if p.alignment_id == "FDL_33_2021_A9_C1"
    ]
    grounded = verifier.verify(query, draft, evidence)
    assert grounded.answerable is False
    assert grounded.verification_status == VerificationStatus.CONTRADICTED


@pytest.mark.legal_safety
def test_permissive_polarity_contrary_to_prohibition(corpus_provisions):
    """Asserting that probation may exceed 6 months contrary to 'shall not exceed' is CONTRADICTED."""
    query = LegalQuery(
        query_id="safety-pol-01",
        text="Can probation exceed 6 months?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    draft = GeneratedLegalAnswer(
        answer_id="ans-pol-test",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Probation may exceed 6 months.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_max_months",
                conclusion="The probation period may exceed 6 months by mutual agreement.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="The probation period may exceed 6 months by mutual agreement. [FDL_33_2021_A9_C1]",
        tool_calls=[],
    )
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    evidence = [
        LegalEvidence(
            evidence_id=f"ev-{p.provision_id}",
            provision=p,
            retrieval_rank=1,
        )
        for p in corpus_provisions
        if p.alignment_id == "FDL_33_2021_A9_C1"
    ]
    grounded = verifier.verify(query, draft, evidence)
    assert grounded.answerable is False
    assert grounded.verification_status == VerificationStatus.CONTRADICTED


@pytest.mark.legal_safety
def test_gratuity_input_validation_safety():
    """NaN, infinite, and negative gratuity inputs must be rejected."""
    with pytest.raises(ValueError, match="non-finite"):
        calculate_gratuity(basic_salary=float("nan"), service_years=3)

    with pytest.raises(ValueError, match="non-finite"):
        calculate_gratuity(basic_salary=10000, service_years=float("inf"))

    with pytest.raises(ValueError, match="positive"):
        calculate_gratuity(basic_salary=-5000, service_years=3)

    with pytest.raises(ValueError, match="non-negative"):
        calculate_gratuity(basic_salary=5000, service_years=-2)


@pytest.mark.legal_safety
def test_gratuity_emirati_national_detection():
    """Queries for UAE national / Emirati worker must calculate 0 gratuity with pension basis."""
    res = calculate_gratuity(basic_salary=15000, service_years=6, is_uae_national=True)
    assert res["total_gratuity"] == 0.0
    assert res["statutory_basis"] == "FDL_33_2021_A51_C1"
    assert res["eligibility_status"] == "ineligible_pension_applicable"
    assert "Pension" in res["note"]


@pytest.mark.legal_safety
def test_gratuity_part_time_pattern_detection():
    """Part-time worker gratuity must refer to Cabinet Resolution 1/2022 Article 30."""
    res = calculate_gratuity(basic_salary=8000, service_years=3, work_pattern="part_time")
    assert res["statutory_basis"] == "CABINET_RES_1_2022_A30_C1"
    assert res["eligibility_status"] == "part_time_prorated"
    assert "Cabinet Resolution No. 1 of 2022 Article 30" in res["note"]


@pytest.mark.legal_safety
def test_gratuity_unpaid_absence_days_deduction():
    """Unpaid absence deduction that pushes service below 1 year must yield ineligible status."""
    # 1.1 years = ~401 days. 60 days unpaid absence reduces effective service to ~0.93 years (< 1 year)
    res = calculate_gratuity(
        basic_salary=10000,
        service_years=Decimal("1.1"),
        unpaid_absence_days=Decimal("60"),
    )
    assert res["total_gratuity"] == 0.0
    assert res["eligibility_status"] == "ineligible_under_one_year"


@pytest.mark.legal_safety
def test_execution_budget_exhaustion_terminates_gracefully(corpus_provisions):
    """When execution step budget is exhausted, agent terminates gracefully with fail-closed response."""
    retriever = InMemoryRetriever(corpus_provisions)
    generator = DeterministicTemplateGenerator()
    valid_ids = {p.alignment_id for p in corpus_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    graph = DeterministicAgentGraph(
        retriever=retriever,
        generator=generator,
        verifier=verifier,
        provisions=corpus_provisions,
    )

    query = LegalQuery(
        query_id="safety-budget-01",
        text="What is the maximum probation period under UAE law?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    # Force max_execution_steps=1 to exhaust budget immediately after analyze_node
    state = AgentState(query=query, max_steps=1, max_execution_steps=1)
    res = graph.invoke(state)

    assert res.answerable is False
    assert res.fallback_reason == "exhausted_budget"
    assert "**Abstention Notice**" in res.answer
    assert any(step.node_name == "abstain_node" for step in res.trace)


@pytest.mark.legal_safety
def test_trace_capacity_preserves_terminal_records():
    """Reaching trace capacity must never drop the terminal abstain_node and finalize_node steps."""
    state = AgentState(
        query=LegalQuery(
            query_id="safety-trace-01",
            text="Query text",
            jurisdiction=Jurisdiction.UAE_ONSHORE,
        ),
        max_trace_steps=10,
    )
    # Fill up non-terminal traces
    for i in range(15):
        state.add_trace(
            node_name="intermediate_node",
            action="step",
            status="success",
            rationale=f"Step {i}",
            duration_ms=1.0,
        )

    # Max non-terminal capacity is max_trace_steps - 2 = 8
    non_terminal_count = len([s for s in state.trace if s.node_name == "intermediate_node"])
    assert non_terminal_count == 8

    # Terminal nodes must be recorded in the reserved slots
    state.add_trace(
        node_name="abstain_node",
        action="abstain",
        status="abstained",
        rationale="Terminal abstention",
        duration_ms=1.0,
    )
    state.add_trace(
        node_name="finalize_node",
        action="finalize",
        status="completed",
        rationale="Terminal finalization",
        duration_ms=1.0,
    )

    assert any(s.node_name == "abstain_node" for s in state.trace)
    assert any(s.node_name == "finalize_node" for s in state.trace)
    assert len(state.trace) == 10


@pytest.mark.legal_safety
def test_sensitive_credentials_redacted_in_trace():
    """Bearer tokens, API keys, and passwords must be masked with [REDACTED] in trace."""
    state = AgentState(
        query=LegalQuery(
            query_id="safety-secret-01",
            text="Query text",
            jurisdiction=Jurisdiction.UAE_ONSHORE,
        )
    )
    state.add_trace(
        node_name="retrieval_node",
        action="search",
        status="success",
        rationale="Using Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret and password=supersecret123",
        duration_ms=1.0,
        input_summary="token=abcdef1234567890abcdef1234567890",
    )
    trace_step = state.trace[0]
    assert "[REDACTED]" in trace_step.rationale
    assert "supersecret123" not in trace_step.rationale
    assert "eyJhbGci" not in trace_step.rationale
    assert "[REDACTED]" in trace_step.input_summary
    assert "abcdef1234567890abcdef1234567890" not in trace_step.input_summary
