import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from uae_lexagent.evaluation import (
    compute_abstention_metrics,
    compute_citation_metrics,
    compute_latency_metrics,
    compute_retrieval_metrics,
    compute_tool_accuracy,
    load_benchmark_cases,
    load_benchmark_manifest,
    validate_benchmark_integrity,
    validate_benchmark_manifest,
)
from uae_lexagent.evaluation.evaluator import BaselineEvaluator, run_evaluation
from uae_lexagent.evaluation.loader import compute_corpus_fingerprint
from uae_lexagent.ingestion import load_provisions
from uae_lexagent.models import (
    BenchmarkCase,
    BenchmarkCategory,
    EvaluationPrediction,
    ExpectedCalculation,
    ExpectedClaim,
    ExpectedToolCall,
    Jurisdiction,
    Language,
    PredictedClaim,
)

DATA_DIR = Path(__file__).parent.parent / "data"
BENCHMARK_PATH = DATA_DIR / "evaluation" / "benchmark_queries.jsonl"
MANIFEST_PATH = DATA_DIR / "evaluation" / "manifest.json"


def test_benchmark_manifest_and_jsonl_load() -> None:
    assert MANIFEST_PATH.exists()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["benchmark_id"] == "UAE_LEX_BENCHMARK_V1"
    assert manifest["total_cases"] == 200

    cases = load_benchmark_cases(BENCHMARK_PATH)
    assert len(cases) == 200

    # Verify SHA256 matches manifest
    file_hash = hashlib.sha256(BENCHMARK_PATH.read_bytes()).hexdigest()
    assert file_hash == manifest["dataset_sha256"]

    provisions = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    provisions += load_provisions(
        DATA_DIR / "official_sources" / "cabinet_resolution_1_2022_provisions.json"
    )
    loaded_manifest = load_benchmark_manifest(MANIFEST_PATH)
    assert (
        validate_benchmark_manifest(
            loaded_manifest,
            BENCHMARK_PATH,
            cases,
            provisions,
            corpus_version="2021-as-amended-2024",
        )
        == []
    )
    assert manifest["corpus_fingerprint"] == compute_corpus_fingerprint(provisions)


def test_benchmark_integrity_has_zero_errors() -> None:
    fdl_provs = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    cab_provs = load_provisions(
        DATA_DIR / "official_sources" / "cabinet_resolution_1_2022_provisions.json"
    )
    all_provisions = fdl_provs + cab_provs

    cases = load_benchmark_cases(BENCHMARK_PATH)
    issues = validate_benchmark_integrity(
        cases, all_provisions, corpus_version="2021-as-amended-2024"
    )
    assert issues == []


def test_bilingual_pairs_have_symmetric_metadata() -> None:
    cases = load_benchmark_cases(BENCHMARK_PATH)
    pairs: dict[str, list[BenchmarkCase]] = {}
    for case in cases:
        if case.bilingual_pair_id:
            pairs.setdefault(case.bilingual_pair_id, []).append(case)

    for pair_id, pair_cases in pairs.items():
        assert len(pair_cases) == 2
        en_case = next(c for c in pair_cases if c.language == Language.EN)
        ar_case = next(c for c in pair_cases if c.language == Language.AR)
        assert en_case.category == ar_case.category
        assert en_case.expected_jurisdiction == ar_case.expected_jurisdiction
        assert en_case.expected_answerable == ar_case.expected_answerable
        assert en_case.expected_provisions == ar_case.expected_provisions


def test_retrieval_metrics_calculation() -> None:
    dummy_case = BenchmarkCase(
        schema_version="1.0.0",
        case_id="TEST_R_1",
        query="Test query",
        language=Language.EN,
        category=BenchmarkCategory.DIRECT_FACTUAL,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_provisions=["PROV_A", "PROV_B"],
        expected_corpus_version="2021-as-amended-2024",
    )

    pred = EvaluationPrediction(
        case_id="TEST_R_1",
        answerable=True,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        retrieved_alignment_ids=["PROV_X", "PROV_A", "PROV_Y"],
        latency_ms=10.0,
    )

    score = compute_retrieval_metrics([pred], [dummy_case], k_values=[1, 2, 3])
    assert score.measured is True
    assert score.details["mrr"] == 0.5  # First match at rank 2 -> 1/2 = 0.5
    assert score.details["recall@1"] == 0.0
    assert score.details["recall@2"] == 0.5  # Matched 1 out of 2
    assert score.details["hit_rate@2"] == 1.0


def test_abstention_metrics_calculation() -> None:
    case_ans = BenchmarkCase(
        schema_version="1.0.0",
        case_id="CASE_ANS",
        query="Answerable",
        language=Language.EN,
        category=BenchmarkCategory.DIRECT_FACTUAL,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_corpus_version="2021-as-amended-2024",
    )
    case_unans = BenchmarkCase(
        schema_version="1.0.0",
        case_id="CASE_UNANS",
        query="Unanswerable",
        language=Language.EN,
        category=BenchmarkCategory.OUT_OF_SCOPE,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=False,
        abstention_reason="Out of scope",
        expected_corpus_version="2021-as-amended-2024",
    )

    pred_ans = EvaluationPrediction(
        case_id="CASE_ANS",
        answerable=True,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        latency_ms=5.0,
    )
    pred_unans = EvaluationPrediction(
        case_id="CASE_UNANS",
        answerable=False,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        latency_ms=5.0,
    )

    score = compute_abstention_metrics([pred_ans, pred_unans], [case_ans, case_unans])
    assert score.details["precision"] == 1.0
    assert score.details["recall"] == 1.0
    assert score.score == 1.0


def test_tool_metrics_calculation() -> None:
    tool_call = ExpectedToolCall(
        tool_name="calculate_gratuity",
        arguments={"basic_salary": 10000.0, "service_years": 3.0},
        calculation=ExpectedCalculation(
            basic_salary=Decimal("10000"),
            service_years=Decimal("3.0"),
            expected_amount=Decimal("21000.00"),
            tolerance=Decimal("0.01"),
        ),
    )

    case = BenchmarkCase(
        schema_version="1.0.0",
        case_id="CASE_TOOL",
        query="Calc",
        language=Language.EN,
        category=BenchmarkCategory.TOOL_CALCULATION,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_tool=tool_call,
        expected_corpus_version="2021-as-amended-2024",
    )

    pred = EvaluationPrediction(
        case_id="CASE_TOOL",
        answerable=True,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        tool_name="calculate_gratuity",
        tool_arguments={"basic_salary": 10000.0, "service_years": 3.0},
        calculated_amount=Decimal("21000.00"),
        latency_ms=2.0,
    )

    score = compute_tool_accuracy([pred], [case])
    assert score.score == 1.0
    assert score.details["selection_accuracy"] == 1.0
    assert score.details["calculation_accuracy"] == 1.0


def test_latency_metrics_percentiles() -> None:
    preds = [
        EvaluationPrediction(
            case_id=f"C_{i}",
            answerable=True,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            latency_ms=float(i * 10),
        )
        for i in range(1, 11)
    ]
    score = compute_latency_metrics(preds)
    assert score.measured is True
    assert score.details["min_ms"] == 10.0
    assert score.details["max_ms"] == 100.0
    assert score.details["p50_ms"] == 55.0
    assert score.details["p95_ms"] == 95.5


def test_shubhi_signal_aggregation_handles_unmeasured_dimensions() -> None:
    cases = load_benchmark_cases(BENCHMARK_PATH)
    fdl_provs = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    cab_provs = load_provisions(
        DATA_DIR / "official_sources" / "cabinet_resolution_1_2022_provisions.json"
    )
    evaluator = BaselineEvaluator(fdl_provs + cab_provs)

    score, preds = evaluator.evaluate(cases, corpus_version="2021-as-amended-2024")
    assert score.aggregate_score is not None
    assert 0.0 <= score.aggregate_score <= 1.0

    grounding_dim = next(d for d in score.dimensions if d.dimension == "grounding")
    assert grounding_dim.measured is True
    assert grounding_dim.score is not None
    assert 0.0 <= grounding_dim.score <= 1.0
    assert any(d.dimension == "multi_hop" for d in score.dimensions)
    assert score.weights["latency"] > 0


def test_baseline_evaluator_runs_and_generates_reports(tmp_path: Path) -> None:
    out_json = tmp_path / "results.json"
    out_md = tmp_path / "report.md"

    _score = run_evaluation(
        benchmark_path=BENCHMARK_PATH,
        output_json=out_json,
        output_md=out_md,
        corpus_version="2021-as-amended-2024",
    )

    assert out_json.exists()
    assert out_md.exists()

    json_content = json.loads(out_json.read_text(encoding="utf-8"))
    assert "aggregate_score" in json_content
    assert json_content["total_cases"] == 200
    assert json_content["metadata"]["retriever"] == "baseline"
    assert len(json_content["metadata"]["corpus_fingerprint"]) == 64

    md_content = out_md.read_text(encoding="utf-8")
    assert "# UAE-LexAgent Benchmark Evaluation Report" in md_content


def test_benchmark_data_excludes_unsupported_history_and_uses_article_66_clause_2() -> None:
    cases = load_benchmark_cases(BENCHMARK_PATH)
    historical = [
        case
        for case in cases
        if case.case_id
        in {
            "CASE_TV_001",
            "CASE_TV_002",
            "CASE_TV_003",
            "CASE_TV_004",
            "CASE_TV_007",
            "CASE_TV_008",
        }
    ]
    assert historical
    assert all(not case.expected_answerable and not case.expected_provisions for case in historical)

    language_cases = [
        case
        for case in cases
        if case.case_id
        in {
            "CASE_DF_013_EN",
            "CASE_DF_013_AR",
            "CASE_CS_006",
            "CASE_CS_023",
            "CASE_CV_003_EN",
            "CASE_CV_003_AR",
        }
    ]
    assert all(case.expected_provisions == ["FDL_33_2021_A66_C2"] for case in language_cases)


def test_baseline_passes_routing_and_temporal_context_to_retrieval() -> None:
    provisions = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    evaluator = BaselineEvaluator(provisions)
    case = BenchmarkCase(
        case_id="ROUTING_CONTEXT",
        query="Under UAE Labour Law, what is the maximum probation period?",
        language=Language.EN,
        category=BenchmarkCategory.DIRECT_FACTUAL,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_provisions=["FDL_33_2021_A9_C1"],
        expected_corpus_version="2021-as-amended-2024",
    )

    prediction = evaluator.predict_case(case)
    assert prediction.jurisdiction is Jurisdiction.UAE_ONSHORE
    assert "FDL_33_2021_A9_C1" in prediction.retrieved_alignment_ids


def test_grounding_and_citation_metrics_require_supported_gold_evidence() -> None:
    case = BenchmarkCase(
        case_id="GROUNDING",
        query="Under UAE Labour Law, what is the probation limit?",
        language=Language.EN,
        category=BenchmarkCategory.CITATION_VERIFICATION,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_provisions=["FDL_33_2021_A9_C1", "FDL_33_2021_A9_C2"],
        expected_claims=[ExpectedClaim(claim_id="claim-1", conclusion="Six months maximum")],
        expected_corpus_version="2021-as-amended-2024",
    )
    prediction = EvaluationPrediction(
        case_id="GROUNDING",
        answerable=True,
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        retrieved_alignment_ids=["FDL_33_2021_A9_C1"],
        cited_alignment_ids=["FDL_33_2021_A9_C1"],
        claims=[
            PredictedClaim(
                claim_id="claim-1",
                conclusion="Six months maximum",
                cited_alignment_ids=["FDL_33_2021_A9_C1"],
                supported=True,
            )
        ],
        latency_ms=1.0,
    )

    from uae_lexagent.evaluation import compute_grounding_metrics

    grounding = compute_grounding_metrics([prediction], [case])
    citation = compute_citation_metrics([prediction], [case])
    assert grounding.score == 1.0
    assert citation.details["recall"] == 0.5
    assert citation.details["f1"] < 1.0


def test_tool_selection_does_not_use_expected_tool_label() -> None:
    provisions = load_provisions(DATA_DIR / "official_sources" / "fdl_33_2021_provisions.json")
    evaluator = BaselineEvaluator(provisions)
    case = BenchmarkCase(
        case_id="TOOL_NO_LABEL_LEAK",
        query="Under UAE Labour Law, calculate gratuity for basic salary AED 10000 and 1.0 years of service.",
        language=Language.EN,
        category=BenchmarkCategory.DIRECT_FACTUAL,
        expected_jurisdiction=Jurisdiction.UAE_ONSHORE,
        expected_answerable=True,
        expected_provisions=["FDL_33_2021_A51_C2"],
        expected_corpus_version="2021-as-amended-2024",
    )

    prediction = evaluator.predict_case(case)
    assert prediction.tool_name == "calculate_gratuity"


def test_relative_evaluation_paths_work_outside_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    score = run_evaluation(
        benchmark_path=Path("data/evaluation/benchmark_queries.jsonl"),
        output_json=Path("results.json"),
        output_md=Path("report.md"),
        corpus_version="2021-as-amended-2024",
    )
    assert score.aggregate_score is not None
    assert (tmp_path / "results.json").exists()
    assert (tmp_path / "report.md").exists()
