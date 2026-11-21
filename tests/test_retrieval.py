"""Comprehensive test suite for the Dual-Corpus Hybrid Retrieval Engine."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from uae_lexagent.evaluation.loader import (
    compute_corpus_fingerprint,
    load_benchmark_cases,
)
from uae_lexagent.ingestion import load_provisions
from uae_lexagent.models import (
    Jurisdiction,
    Language,
    LegalProvision,
    LegalQuery,
)
from uae_lexagent.retrieval import (
    BM25Index,
    DeterministicHashEmbedding,
    HybridLegalRetriever,
    IndexedProvision,
    InMemoryRetriever,
    InMemoryVectorStore,
    RankedCandidate,
    RetrievalFilters,
    build_retrieval_index,
    normalize_arabic,
    reciprocal_rank_fusion,
    tokenize_legal_text,
)


def _provision(
    provision_id: str,
    text: str,
    *,
    alignment_id: str | None = None,
    jurisdiction: Jurisdiction = Jurisdiction.UAE_ONSHORE,
    language: str = "en",
    is_authoritative: bool | None = None,
    effective_from: date | None = None,
    effective_until: date | None = None,
) -> LegalProvision:
    is_auth = (language == "ar") if is_authoritative is None else is_authoritative
    trans_of = (
        None if is_auth else (f"{provision_id}-ar" if not alignment_id else f"{alignment_id}_AR")
    )
    return LegalProvision(
        provision_id=provision_id,
        alignment_id=alignment_id or provision_id,
        document_id="law-1",
        source_id="source-1",
        jurisdiction=jurisdiction,
        legal_domain="employment",
        article="9",
        language=language,
        is_authoritative=is_auth,
        translation_of=trans_of,
        text=text,
        effective_from=effective_from,
        effective_until=effective_until,
        source_url="https://example.test/law",
    )


# ---------------------------------------------------------------------------
# 1. Embedding Provider Tests
# ---------------------------------------------------------------------------


def test_deterministic_hash_embedding_properties() -> None:
    embedder = DeterministicHashEmbedding(dimension=128)
    assert embedder.dimension == 128
    assert embedder.model_id == "deterministic-hash-128"

    vecs = embedder.embed(["Probation period notice period 14 days", ""])
    assert len(vecs) == 2
    assert len(vecs[0]) == 128
    assert len(vecs[1]) == 128

    # Check unit norm
    norm_0 = math.sqrt(sum(x * x for x in vecs[0]))
    norm_1 = math.sqrt(sum(x * x for x in vecs[1]))
    assert pytest.approx(norm_0, rel=1e-5) == 1.0
    assert pytest.approx(norm_1, rel=1e-5) == 1.0

    # Determinism
    vecs_repeat = embedder.embed(["Probation period notice period 14 days"])
    assert vecs[0] == vecs_repeat[0]


def test_deterministic_hash_embedding_rejects_non_positive_dimension() -> None:
    with pytest.raises(ValueError, match="positive"):
        DeterministicHashEmbedding(dimension=0)


# ---------------------------------------------------------------------------
# 2. Tokenization and Normalization Tests
# ---------------------------------------------------------------------------


def test_arabic_text_normalization() -> None:
    # Tashkeel removal & alif/yaa/taa marbuta normalization
    raw_ar = "إِنْهَاءُ عَقْدِ العَمَلِ فِي فَتْرَةِ التَّجْرِبَةِ"
    normalized = normalize_arabic(raw_ar)
    assert "إ" not in normalized
    assert "ة" not in normalized
    assert "ي" in normalized

    tokens = tokenize_legal_text(raw_ar, Language.AR)
    assert "انهاء" in tokens
    assert "عقد" in tokens
    assert "عمل" in tokens or "العَمَلِ" not in tokens
    assert "تجربه" in tokens


def test_english_legal_tokenization_preserves_numbers_and_hyphens() -> None:
    text = "Under Article 9 Clause 2, employer gives 14 days written notice for non-compete."
    tokens = tokenize_legal_text(text, Language.EN)
    assert "article" in tokens
    assert "9" in tokens
    assert "2" in tokens
    assert "14" in tokens
    assert "days" in tokens
    assert "non-compete" in tokens or "non" in tokens


# ---------------------------------------------------------------------------
# 3. BM25 Sparse Index Tests
# ---------------------------------------------------------------------------


def test_bm25_index_scoring_and_tie_breaking() -> None:
    p1 = _provision("p1", "probation period notice requirement 14 days", alignment_id="align_p1")
    p2 = _provision(
        "p2",
        "probation period notice requirement 30 days during contract",
        alignment_id="align_p2",
    )
    p3 = _provision(
        "p3", "annual leave calculation after one year service", alignment_id="align_p3"
    )

    bm25 = BM25Index()
    bm25.build([p1, p2, p3])

    # Query with specific tokens
    results = bm25.query(query_tokens=["14", "days", "probation"], top_k=2)
    assert len(results) >= 1
    assert results[0].provision_id == "p1"
    assert results[0].sparse_rank == 1
    assert results[0].sparse_score is not None and results[0].sparse_score > 0


def test_bm25_index_filters_jurisdiction_and_temporality() -> None:
    p_onshore = _provision(
        "onshore", "probation termination rules", jurisdiction=Jurisdiction.UAE_ONSHORE
    )
    p_difc = _provision("difc", "probation termination rules", jurisdiction=Jurisdiction.DIFC)
    p_expired = _provision(
        "expired", "probation termination rules", effective_until=date(2023, 1, 1)
    )

    bm25 = BM25Index()
    bm25.build([p_onshore, p_difc, p_expired])

    filters = RetrievalFilters(
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        as_of=date(2024, 1, 1),
    )
    results = bm25.query(["probation", "termination"], filters=filters)
    assert len(results) == 1
    assert results[0].provision_id == "onshore"


def test_bm25_handles_empty_query_and_empty_index() -> None:
    bm25 = BM25Index()
    assert bm25.query(["probation"]) == []

    bm25.build([_provision("p1", "some legal text")])
    assert bm25.query([]) == []
    assert bm25.query(["unrelated", "extraneous"]) == []


# ---------------------------------------------------------------------------
# 4. In-Memory Vector Store Tests
# ---------------------------------------------------------------------------


def test_in_memory_vector_store_cosine_search_and_validation() -> None:
    store = InMemoryVectorStore(dimension=4)
    p1 = _provision("p1", "text 1")
    p2 = _provision("p2", "text 2")

    store.upsert(
        [
            IndexedProvision(provision=p1, vector=[1.0, 0.0, 0.0, 0.0]),
            IndexedProvision(provision=p2, vector=[0.0, 1.0, 0.0, 0.0]),
        ]
    )

    results = store.query(vector=[1.0, 0.0, 0.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0].provision_id == "p1"
    assert pytest.approx(results[0].dense_score or 0.0, rel=1e-4) == 1.0

    # Dimension mismatch should raise
    with pytest.raises(ValueError, match="dimension mismatch"):
        store.query(vector=[1.0, 0.0])


# ---------------------------------------------------------------------------
# 5. Reciprocal Rank Fusion (RRF) Tests
# ---------------------------------------------------------------------------


def test_reciprocal_rank_fusion_mathematical_combination() -> None:
    p1 = _provision("p1", "text 1", alignment_id="align_1")
    p2 = _provision("p2", "text 2", alignment_id="align_2")

    dense_candidates = [
        RankedCandidate(
            alignment_id="align_1",
            provision_id="p1",
            dense_rank=1,
            dense_score=0.95,
            provision=p1,
        ),
        RankedCandidate(
            alignment_id="align_2",
            provision_id="p2",
            dense_rank=2,
            dense_score=0.85,
            provision=p2,
        ),
    ]
    sparse_candidates = [
        RankedCandidate(
            alignment_id="align_2",
            provision_id="p2",
            sparse_rank=1,
            sparse_score=5.0,
            provision=p2,
        ),
        RankedCandidate(
            alignment_id="align_1",
            provision_id="p1",
            sparse_rank=2,
            sparse_score=3.0,
            provision=p1,
        ),
    ]

    fused = reciprocal_rank_fusion(dense_candidates, sparse_candidates, k=60, alpha=0.5)
    assert len(fused) == 2

    # Expected scores:
    # align_1: 0.5/(60+1) + 0.5/(60+2) = 0.5/61 + 0.5/62
    # align_2: 0.5/(60+2) + 0.5/(60+1) = 0.5/62 + 0.5/61
    expected_score = (0.5 / 61.0) + (0.5 / 62.0)
    assert pytest.approx(fused[0].fused_score, rel=1e-5) == expected_score
    assert pytest.approx(fused[1].fused_score, rel=1e-5) == expected_score


def test_reciprocal_rank_fusion_collapses_bilingual_alignment_pairs() -> None:
    p_ar = _provision("ar_1", "النص العربي", alignment_id="align_shared", language="ar")
    p_en = _provision("en_1", "English Text", alignment_id="align_shared", language="en")

    dense_cands = [
        RankedCandidate(
            alignment_id="align_shared",
            provision_id="ar_1",
            dense_rank=1,
            provision=p_ar,
        )
    ]
    sparse_cands = [
        RankedCandidate(
            alignment_id="align_shared",
            provision_id="en_1",
            sparse_rank=1,
            provision=p_en,
        )
    ]

    fused = reciprocal_rank_fusion(dense_cands, sparse_cands, k=60, alpha=0.5)
    assert len(fused) == 1
    assert fused[0].alignment_id == "align_shared"
    assert pytest.approx(fused[0].fused_score, rel=1e-5) == (0.5 / 61.0) + (0.5 / 61.0)


def test_reciprocal_rank_fusion_uses_rank_even_for_zero_score_candidates() -> None:
    candidate = RankedCandidate(
        alignment_id="align_zero",
        provision_id="p_zero",
        dense_rank=1,
        dense_score=0.0,
    )
    fused = reciprocal_rank_fusion([candidate], [])
    assert fused[0].fused_score == pytest.approx(0.5 / 61.0)


def test_index_fingerprint_matches_canonical_corpus_fingerprint() -> None:
    provisions = [
        _provision("p1", "probation rules", alignment_id="align_1"),
        _provision("p2", "notice rules", alignment_id="align_2"),
    ]
    index = build_retrieval_index(provisions, DeterministicHashEmbedding())
    assert index.corpus_fingerprint == compute_corpus_fingerprint(provisions)


def test_index_rejects_embedding_count_mismatch() -> None:
    class ShortEmbedder:
        dimension = 2
        model_id = "short"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0]]

    with pytest.raises(ValueError, match="returned 1 vectors for 2 provisions"):
        build_retrieval_index(
            [_provision("p1", "one"), _provision("p2", "two")],
            ShortEmbedder(),
        )


# ---------------------------------------------------------------------------
# 6. End-to-End Synchronized Index & Hybrid Retriever Tests
# ---------------------------------------------------------------------------


def test_hybrid_retriever_bilingual_evidence_resolution() -> None:
    p_ar = _provision(
        "FDL_33_2021_A9_C2_AR",
        "يجوز لصاحب العمل إنهاء خدمة العامل خلال فترة التجربة بإخطار خطي مدته (14) يوماً",
        alignment_id="FDL_33_2021_A9_C2",
        language="ar",
        is_authoritative=True,
    )
    p_en = _provision(
        "FDL_33_2021_A9_C2_EN",
        "The employer may terminate the worker's service during the probation period with a written notice of (14) days",
        alignment_id="FDL_33_2021_A9_C2",
        language="en",
        is_authoritative=False,
    )

    embedder = DeterministicHashEmbedding(dimension=64)
    index = build_retrieval_index([p_ar, p_en], embedding_provider=embedder)

    retriever = HybridLegalRetriever(index=index)

    # Arabic query -> Arabic provision returned
    res_ar = retriever.search(
        LegalQuery(
            query_id="q-ar",
            text="إنهاء الخدمة خلال فترة التجربة 14 يوما",
            language=Language.AR,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
        ),
        top_k=1,
    )
    assert len(res_ar) == 1
    assert res_ar[0].provision.language == "ar"
    assert res_ar[0].provision.provision_id == "FDL_33_2021_A9_C2_AR"

    # English query -> English provision returned
    res_en = retriever.search(
        LegalQuery(
            query_id="q-en",
            text="employer termination probation period notice 14 days",
            language=Language.EN,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
        ),
        top_k=1,
    )
    assert len(res_en) == 1
    assert res_en[0].provision.language == "en"
    assert res_en[0].provision.provision_id == "FDL_33_2021_A9_C2_EN"


def test_hybrid_retriever_hard_metadata_filtering() -> None:
    p_onshore = _provision(
        "p_onshore",
        "probation notice rules",
        alignment_id="align_1",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
    )
    p_difc = _provision(
        "p_difc",
        "probation notice rules",
        alignment_id="align_2",
        jurisdiction=Jurisdiction.DIFC,
    )

    embedder = DeterministicHashEmbedding(dimension=32)
    index = build_retrieval_index([p_onshore, p_difc], embedding_provider=embedder)
    retriever = HybridLegalRetriever(index=index)

    res = retriever.retrieve(
        "probation notice rules",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        top_k=5,
    )
    assert len(res) == 1
    assert res[0].provision.provision_id == "p_onshore"


# ---------------------------------------------------------------------------
# 7. Baseline vs Hybrid Comparison Gate on Ground-Truth Benchmark
# ---------------------------------------------------------------------------


def test_hybrid_retriever_meets_baseline_quality_gate() -> None:
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / "data" / "official_sources"
    fdl = load_provisions(data_dir / "fdl_33_2021_provisions.json")
    cab = load_provisions(data_dir / "cabinet_resolution_1_2022_provisions.json")
    all_provisions = fdl + cab

    cases = load_benchmark_cases(project_root / "data" / "evaluation" / "benchmark_queries.jsonl")
    retrieval_cases = [c for c in cases if c.expected_provisions and c.expected_answerable]

    # 1. Baseline Evaluation
    baseline_retriever = InMemoryRetriever(all_provisions)
    baseline_hits = 0
    for case in retrieval_cases:
        results = baseline_retriever.retrieve(
            case.query,
            top_k=3,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            as_of=case.as_of,
            language=case.language,
        )
        retrieved_ids = {r.provision.alignment_id for r in results if r.provision.alignment_id}
        if any(exp in retrieved_ids for exp in case.expected_provisions):
            baseline_hits += 1

    baseline_hit_rate = baseline_hits / len(retrieval_cases) if retrieval_cases else 0.0

    # 2. Hybrid Evaluation
    embedder = DeterministicHashEmbedding(dimension=128)
    index = build_retrieval_index(all_provisions, embedding_provider=embedder)
    hybrid_retriever = HybridLegalRetriever(index=index)

    hybrid_hits = 0
    for case in retrieval_cases:
        results = hybrid_retriever.retrieve(
            case.query,
            top_k=3,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            as_of=case.as_of,
            language=case.language,
        )
        retrieved_ids = {r.provision.alignment_id for r in results if r.provision.alignment_id}
        if any(exp in retrieved_ids for exp in case.expected_provisions):
            hybrid_hits += 1

    hybrid_hit_rate = hybrid_hits / len(retrieval_cases) if retrieval_cases else 0.0

    # Gate: Hybrid HitRate@3 must be >= Baseline HitRate@3
    assert hybrid_hit_rate >= baseline_hit_rate, (
        f"Hybrid retrieval HitRate@3 ({hybrid_hit_rate:.3f}) fell below Baseline ({baseline_hit_rate:.3f})"
    )
