"""Comprehensive test suite for legal synthesis, fact extraction, and citation verification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from uae_lexagent.ingestion import load_provisions
from uae_lexagent.models import (
    Jurisdiction,
    Language,
    LegalEvidence,
    LegalProvision,
    LegalQuery,
)
from uae_lexagent.retrieval import (
    HybridLegalRetriever,
    InMemoryRetriever,
    build_retrieval_index,
    get_embedding_provider,
)
from uae_lexagent.synthesis import (
    CitationVerifier,
    DeterministicTemplateGenerator,
    DraftLegalClaim,
    GeneratedLegalAnswer,
    LegalSynthesisEngine,
    VerificationStatus,
    evaluate_answerability,
    extract_legal_facts,
)


@pytest.fixture
def official_provisions() -> list[LegalProvision]:
    root = Path(__file__).resolve().parents[1]
    fdl = load_provisions(root / "data" / "official_sources" / "fdl_33_2021_provisions.json")
    cab = load_provisions(
        root / "data" / "official_sources" / "cabinet_resolution_1_2022_provisions.json"
    )
    return fdl + cab


@pytest.fixture
def mock_evidence(official_provisions: list[LegalProvision]) -> list[LegalEvidence]:
    prov_map = {p.alignment_id: p for p in official_provisions if p.alignment_id}
    p_c1 = prov_map["FDL_33_2021_A9_C1"]
    p_c2 = prov_map["FDL_33_2021_A9_C2"]
    return [
        LegalEvidence(
            evidence_id="ev-1",
            provision=p_c1,
            retrieval_rank=1,
            dense_score=0.9,
            sparse_score=10.0,
        ),
        LegalEvidence(
            evidence_id="ev-2",
            provision=p_c2,
            retrieval_rank=2,
            dense_score=0.85,
            sparse_score=9.0,
        ),
    ]


def test_fact_extraction_english():
    text = "The probation period shall not exceed 6 months. The employer may terminate giving 14 days notice."
    facts = extract_legal_facts(text)
    assert len(facts) >= 1
    f = facts[0]
    assert Decimal("6") in f.numeric_values or Decimal("14") in f.numeric_values


def test_fact_extraction_arabic():
    text = "لا يجوز أن تزيد فترة التجربة على (6) ستة أشهر من تاريخ بدء العمل، ويجوز لصاحب العمل الإنهاء بإخطار مدته (14) يوماً."
    facts = extract_legal_facts(text)
    assert len(facts) >= 1
    f = facts[0]
    assert Decimal("6") in f.numeric_values
    assert Decimal("14") in f.numeric_values


def test_answerability_difc_and_adgm():
    q_difc = LegalQuery(
        query_id="q1",
        text="What is probation under DIFC employment law?",
        jurisdiction=Jurisdiction.DIFC,
    )
    d1 = evaluate_answerability(q_difc)
    assert not d1.answerable
    assert d1.reason == "unsupported_jurisdiction"

    q_adgm = LegalQuery(
        query_id="q2",
        text="What are ADGM non-compete rules?",
        jurisdiction=Jurisdiction.ADGM,
    )
    d2 = evaluate_answerability(q_adgm)
    assert not d2.answerable
    assert d2.reason == "unsupported_jurisdiction"


def test_answerability_unknown_article():
    q = LegalQuery(query_id="q3", text="Explain Article 95 of UAE Labour Law.")
    d = evaluate_answerability(q)
    assert not d.answerable
    assert d.reason == "unknown_article"


def test_answerability_historical_gap():
    q = LegalQuery(query_id="q4", text="What was probation in 2020?", as_of=date(2020, 1, 1))
    d = evaluate_answerability(q)
    assert not d.answerable
    assert d.reason == "historical_corpus_gap"


def test_answerability_out_of_scope_worker():
    q = LegalQuery(
        query_id="q5",
        text="What are the probation rules for domestic workers / housemaids?",
    )
    d = evaluate_answerability(q)
    assert not d.answerable
    assert d.reason == "out_of_scope_worker_category"


def test_deterministic_generator_probation_duration(mock_evidence: list[LegalEvidence]):
    gen = DeterministicTemplateGenerator()
    q = LegalQuery(
        query_id="q_prob",
        text="What is the maximum duration of probation under UAE Labour Law?",
    )
    ans = gen.generate(
        query=q,
        evidence=mock_evidence,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        language=Language.EN,
    )
    assert ans.answerable
    assert len(ans.claims) == 1
    assert ans.claims[0].claim_key == "probation_max_months"
    assert "FDL_33_2021_A9_C1" in ans.claims[0].cited_alignment_ids


def test_verifier_validates_supported_claim(
    mock_evidence: list[LegalEvidence], official_provisions: list[LegalProvision]
):
    valid_ids = {p.alignment_id for p in official_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    q = LegalQuery(query_id="q_test", text="What is probation duration?")

    draft = GeneratedLegalAnswer(
        answer_id="ans-1",
        query=q,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Probation cannot exceed 6 months.",
        claims=[
            DraftLegalClaim(
                claim_id="c1",
                claim_key="probation_max_months",
                conclusion="Probation shall not exceed 6 months.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="Probation shall not exceed 6 months. [FDL_33_2021_A9_C1]",
        referenced_evidence=mock_evidence,
    )

    grounded = verifier.verify(q, draft, mock_evidence)
    assert grounded.answerable
    assert grounded.verification_status == VerificationStatus.VERIFIED
    assert len(grounded.claims) == 1
    assert grounded.claims[0].verification_status == VerificationStatus.VERIFIED


def test_verifier_flags_invalid_citation_and_unsupported_claim(
    mock_evidence: list[LegalEvidence],
):
    verifier = CitationVerifier(valid_corpus_alignment_ids={"FDL_33_2021_A9_C1"}, strict=True)
    q = LegalQuery(query_id="q_bad_cit", text="Notice period?")

    draft = GeneratedLegalAnswer(
        answer_id="ans-2",
        query=q,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Employer notice is 14 days.",
        claims=[
            DraftLegalClaim(
                claim_id="c2",
                claim_key="probation_employer_notice_days",
                conclusion="Employer notice is 14 days.",
                cited_alignment_ids=["FICTITIOUS_ARTICLE_99"],
            )
        ],
        full_text="Employer notice is 14 days. [FICTITIOUS_ARTICLE_99]",
        referenced_evidence=mock_evidence,
    )

    grounded = verifier.verify(q, draft, mock_evidence)
    assert not grounded.answerable
    assert grounded.verification_status == VerificationStatus.UNVERIFIED
    assert grounded.claims[0].verification_status == VerificationStatus.UNVERIFIED


def test_verifier_detects_numeric_contradiction(
    mock_evidence: list[LegalEvidence], official_provisions: list[LegalProvision]
):
    valid_ids = {p.alignment_id for p in official_provisions if p.alignment_id}
    verifier = CitationVerifier(valid_corpus_alignment_ids=valid_ids, strict=True)
    q = LegalQuery(query_id="q_contra", text="Employer notice?")

    # Evidence FDL_33_2021_A9_C2 specifies 14 days, but draft claims 45 days
    draft = GeneratedLegalAnswer(
        answer_id="ans-3",
        query=q,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Employer notice is 45 days.",
        claims=[
            DraftLegalClaim(
                claim_id="c3",
                claim_key="probation_employer_notice_days",
                conclusion="The employer must give at least 45 days written notice.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            )
        ],
        full_text="The employer must give at least 45 days written notice. [FDL_33_2021_A9_C1]",
        referenced_evidence=mock_evidence,
    )

    grounded = verifier.verify(q, draft, mock_evidence)
    assert not grounded.answerable
    assert grounded.verification_status == VerificationStatus.CONTRADICTED
    assert grounded.claims[0].verification_status == VerificationStatus.CONTRADICTED


def test_synthesis_engine_end_to_end(official_provisions: list[LegalProvision]):
    embedder = get_embedding_provider("hash")
    index = build_retrieval_index(official_provisions, embedding_provider=embedder)
    retriever = HybridLegalRetriever(index=index)
    engine = LegalSynthesisEngine(retriever=retriever, provisions=official_provisions)

    q = LegalQuery(
        query_id="q_e2e",
        text="Under UAE Labour Law, what is the maximum probation period?",
    )
    ans = engine.synthesize(q)
    assert ans.answerable
    assert ans.verification_status == VerificationStatus.VERIFIED
    assert len(ans.claims) > 0
    assert any(c.verification_status == VerificationStatus.VERIFIED for c in ans.claims)
    assert "FDL_33_2021_A9_C1" in ans.claims[0].cited_alignment_ids


def test_synthesis_engine_abstains_on_difc(official_provisions: list[LegalProvision]):
    retriever = InMemoryRetriever(official_provisions)
    engine = LegalSynthesisEngine(retriever=retriever, provisions=official_provisions)

    q = LegalQuery(
        query_id="q_difc_e2e",
        text="How does DIFC court handle probation termination?",
        jurisdiction=Jurisdiction.DIFC,
    )
    ans = engine.synthesize(q)
    assert not ans.answerable
    assert ans.verification_status == VerificationStatus.ABSTAINED
    assert ans.abstention_reason == "unsupported_jurisdiction"


def test_engine_abstains_when_jurisdiction_is_ambiguous(
    official_provisions: list[LegalProvision],
):
    engine = LegalSynthesisEngine(
        InMemoryRetriever(official_provisions), provisions=official_provisions
    )
    answer = engine.synthesize(
        LegalQuery(query_id="q_ambiguous", text="What is the maximum probation period?")
    )
    assert not answer.answerable
    assert answer.abstention_reason == "jurisdiction_ambiguous"
    assert "No supported template" not in answer.full_text


def test_engine_rejects_article_missing_from_indexed_corpus(
    official_provisions: list[LegalProvision],
):
    engine = LegalSynthesisEngine(
        InMemoryRetriever(official_provisions), provisions=official_provisions
    )
    answer = engine.synthesize(
        LegalQuery(
            query_id="q_missing_article",
            text="Under UAE Labour Law, explain Article 2.",
        )
    )
    assert not answer.answerable
    assert answer.abstention_reason == "unknown_article"


def test_strict_verifier_abstains_on_mixed_claim_set(
    mock_evidence: list[LegalEvidence], official_provisions: list[LegalProvision]
):
    verifier = CitationVerifier(
        valid_corpus_alignment_ids={p.alignment_id for p in official_provisions if p.alignment_id},
        strict=True,
    )
    query = LegalQuery(query_id="q_mixed", text="Under UAE Labour Law, what is probation?")
    draft = GeneratedLegalAnswer(
        answer_id="ans-mixed",
        query=query,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        answerable=True,
        summary="Mixed claims",
        claims=[
            DraftLegalClaim(
                claim_id="valid",
                claim_key="probation_max_months",
                conclusion="Probation shall not exceed 6 months.",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
            ),
            DraftLegalClaim(
                claim_id="invalid",
                claim_key="probation_max_months",
                conclusion="Probation shall not exceed 6 months.",
                cited_alignment_ids=["NOT_IN_CORPUS"],
            ),
        ],
        full_text="Probation shall not exceed 6 months. [FDL_33_2021_A9_C1]",
    )
    answer = verifier.verify(query, draft, mock_evidence)
    assert not answer.answerable
    assert answer.verification_status == VerificationStatus.UNVERIFIED


def test_deterministic_generator_ids_are_reproducible(
    mock_evidence: list[LegalEvidence],
):
    generator = DeterministicTemplateGenerator()
    query = LegalQuery(
        query_id="q_reproducible",
        text="Under UAE Labour Law, what is the maximum probation period?",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
    )
    first = generator.generate(query, mock_evidence, Jurisdiction.UAE_ONSHORE, Language.EN)
    second = generator.generate(query, mock_evidence, Jurisdiction.UAE_ONSHORE, Language.EN)
    assert first.answer_id == second.answer_id
    assert [claim.claim_id for claim in first.claims] == [claim.claim_id for claim in second.claims]
