"""Comprehensive metric calculation engine for the Shubhi Signal evaluation framework."""

from __future__ import annotations

import statistics
from decimal import Decimal

from ..models import (
    BenchmarkCase,
    BenchmarkCategory,
    DimensionScore,
    EvaluationPrediction,
    Language,
    ShubhiSignalScore,
    LegalProvision,
)

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    "jurisdiction": 0.12,
    "retrieval": 0.14,
    "grounding": 0.14,
    "citation": 0.08,
    "tool_accuracy": 0.12,
    "bilingual_consistency": 0.10,
    "abstention": 0.10,
    "temporal": 0.10,
    "multi_hop": 0.04,
    "latency": 0.06,
}


def compute_jurisdiction_accuracy(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Measure exact jurisdiction classification accuracy across all cases."""
    case_map = {c.case_id: c for c in cases}
    correct = 0
    total = 0

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case:
            continue
        total += 1
        if pred.jurisdiction == case.expected_jurisdiction:
            correct += 1

    score = (correct / total) if total > 0 else 0.0
    return DimensionScore(
        dimension="jurisdiction",
        score=score,
        sample_count=total,
        measured=True,
        details={"correct": correct, "total": total},
    )


def compute_retrieval_metrics(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    k_values: list[int] | None = None,
) -> DimensionScore:
    """Compute Recall@K, HitRate@K, and Mean Reciprocal Rank (MRR) against expected provisions."""
    if k_values is None:
        k_values = [1, 3, 5]

    case_map = {c.case_id: c for c in cases}
    recalls: dict[int, list[float]] = {k: [] for k in k_values}
    hit_rates: dict[int, list[float]] = {k: [] for k in k_values}
    rr_scores: list[float] = []
    evaluated_cases = 0

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case or not case.expected_provisions or not case.expected_answerable:
            continue

        evaluated_cases += 1
        expected_set = set(case.expected_provisions)
        retrieved = pred.retrieved_alignment_ids

        # Reciprocal Rank
        first_rank: int | None = None
        for rank, prov_id in enumerate(retrieved, 1):
            if prov_id in expected_set:
                first_rank = rank
                break
        rr_scores.append(1.0 / first_rank if first_rank is not None else 0.0)

        # Recall@K and HitRate@K
        for k in k_values:
            top_k = set(retrieved[:k])
            matched = top_k & expected_set
            recalls[k].append(len(matched) / len(expected_set) if expected_set else 0.0)
            hit_rates[k].append(1.0 if len(matched) > 0 else 0.0)

    mrr = statistics.mean(rr_scores) if rr_scores else 0.0
    mean_recalls = {f"recall@{k}": statistics.mean(recalls[k]) if recalls[k] else 0.0 for k in k_values}
    mean_hit_rates = {f"hit_rate@{k}": statistics.mean(hit_rates[k]) if hit_rates[k] else 0.0 for k in k_values}

    # Use HitRate@3 as canonical retrieval dimension score
    canonical_score = mean_hit_rates.get("hit_rate@3", mrr)

    return DimensionScore(
        dimension="retrieval",
        score=canonical_score,
        sample_count=evaluated_cases,
        measured=True,
        details={
            "mrr": mrr,
            **mean_recalls,
            **mean_hit_rates,
        },
    )


def compute_grounding_metrics(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Measure claim-level evidence grounding."""
    case_map = {c.case_id: c for c in cases}
    expected_claim_count = 0
    predicted_claim_count = 0
    grounded_claim_count = 0
    evaluated_cases = 0

    for prediction in predictions:
        case = case_map.get(prediction.case_id)
        if not case or not case.expected_claims:
            continue
        evaluated_cases += 1
        expected_by_id = {claim.claim_id: claim for claim in case.expected_claims}
        predicted_claim_count += len(prediction.claims)
        expected_claim_count += len(expected_by_id)
        expected_provisions = set(case.expected_provisions)
        for claim in prediction.claims:
            expected = expected_by_id.get(claim.claim_id)
            if not expected:
                continue
            has_expected_evidence = bool(set(claim.cited_alignment_ids) & expected_provisions)
            if claim.supported is not False and (has_expected_evidence or not expected_provisions):
                grounded_claim_count += 1

    if not evaluated_cases or not predicted_claim_count:
        return DimensionScore(
            dimension="grounding",
            score=None,
            sample_count=0,
            measured=False,
            details={"note": "No predicted claim sets were supplied."},
        )

    precision = grounded_claim_count / predicted_claim_count if predicted_claim_count else 0.0
    recall = grounded_claim_count / expected_claim_count if expected_claim_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return DimensionScore(
        dimension="grounding",
        score=f1,
        sample_count=evaluated_cases,
        measured=True,
        details={
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "grounded_claims": grounded_claim_count,
            "expected_claims": expected_claim_count,
        },
    )


def compute_citation_metrics(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    valid_corpus_alignment_ids: set[str] | None = None,
) -> DimensionScore:
    """Measure validity and precision of cited canonical alignment IDs."""
    case_map = {c.case_id: c for c in cases}
    total_cited = 0
    valid_cited = 0
    expected_matches = 0
    total_expected = 0
    per_case_f1: list[float] = []

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case:
            continue

        cited = set(pred.cited_alignment_ids)
        expected = set(case.expected_provisions)

        total_cited += len(cited)
        if valid_corpus_alignment_ids is not None:
            valid_cited += len(cited & valid_corpus_alignment_ids)
        else:
            valid_cited += len(cited)

        if expected:
            total_expected += len(expected)
            expected_matches += len(cited & expected)
            precision_case = len(cited & expected) / len(cited) if cited else 0.0
            recall_case = len(cited & expected) / len(expected)
        else:
            precision_case = 1.0 if not cited else 0.0
            recall_case = precision_case
        per_case_f1.append(
            2 * precision_case * recall_case / (precision_case + recall_case)
            if precision_case + recall_case
            else 0.0
        )

    precision = (expected_matches / total_cited) if total_cited > 0 else 0.0
    recall = (expected_matches / total_expected) if total_expected > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    validity_rate = (valid_cited / total_cited) if total_cited > 0 else 1.0

    return DimensionScore(
        dimension="citation",
        score=statistics.mean(per_case_f1) if per_case_f1 else None,
        sample_count=len(per_case_f1),
        measured=bool(per_case_f1),
        details={
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "macro_f1": statistics.mean(per_case_f1) if per_case_f1 else 0.0,
            "validity_rate": validity_rate,
            "total_cited": total_cited,
        },
    )


def compute_tool_accuracy(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Measure tool selection, argument extraction exact match, and calculation accuracy."""
    case_map = {c.case_id: c for c in cases}
    total_tool_cases = 0
    correct_selection = 0
    correct_arguments = 0
    correct_calculations = 0
    total_calculation_cases = 0

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case or not case.expected_tool:
            continue

        total_tool_cases += 1
        expected_tool = case.expected_tool

        if pred.tool_name == expected_tool.tool_name:
            correct_selection += 1

            # Match arguments
            if _arguments_equal(pred.tool_arguments, expected_tool.arguments):
                correct_arguments += 1

            # Match calculation
            if expected_tool.calculation:
                total_calculation_cases += 1
            if expected_tool.calculation and pred.calculated_amount is not None:
                exp_amt = expected_tool.calculation.expected_amount
                tol = expected_tool.calculation.tolerance
                if abs(pred.calculated_amount - exp_amt) <= tol:
                    correct_calculations += 1

    selection_acc = (correct_selection / total_tool_cases) if total_tool_cases > 0 else 0.0
    arg_acc = (correct_arguments / total_tool_cases) if total_tool_cases > 0 else 0.0
    calc_acc = (correct_calculations / total_calculation_cases) if total_calculation_cases > 0 else None

    components = [selection_acc, arg_acc]
    if calc_acc is not None:
        components.append(calc_acc)
    overall_tool_score = sum(components) / len(components) if components else 0.0

    return DimensionScore(
        dimension="tool_accuracy",
        score=overall_tool_score,
        sample_count=total_tool_cases,
        measured=True if total_tool_cases > 0 else False,
        details={
            "selection_accuracy": selection_acc,
            "argument_accuracy": arg_acc,
            "calculation_accuracy": calc_acc,
            "total_cases": total_tool_cases,
            "total_calculation_cases": total_calculation_cases,
        },
    )


def _arguments_equal(actual: dict[str, object], expected: dict[str, object]) -> bool:
    """Compare tool arguments without making numeric representation significant."""
    if actual.keys() != expected.keys():
        return False
    for key in actual:
        actual_value = actual[key]
        expected_value = expected[key]
        if isinstance(actual_value, (int, float, Decimal)) and isinstance(expected_value, (int, float, Decimal)):
            if Decimal(str(actual_value)) != Decimal(str(expected_value)):
                return False
        elif actual_value != expected_value:
            return False
    return True


def compute_bilingual_consistency(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Evaluate whether EN and AR query pairs yield consistent decisions and retrieved references."""
    case_map = {c.case_id: c for c in cases}
    pred_map = {p.case_id: p for p in predictions}

    pairs: dict[str, list[BenchmarkCase]] = {}
    for case in cases:
        if case.bilingual_pair_id:
            pairs.setdefault(case.bilingual_pair_id, []).append(case)

    total_pairs = 0
    consistent_pairs = 0

    for pair_id, pair_cases in pairs.items():
        if len(pair_cases) != 2:
            continue
        c_en = next((c for c in pair_cases if c.language == Language.EN), None)
        c_ar = next((c for c in pair_cases if c.language == Language.AR), None)
        if not c_en or not c_ar:
            continue

        p_en = pred_map.get(c_en.case_id)
        p_ar = pred_map.get(c_ar.case_id)
        if not p_en or not p_ar:
            continue

        total_pairs += 1

        # Consistency requires both language variants to reach the same gold reference.
        jurisdiction_match = (p_en.jurisdiction == p_ar.jurisdiction)
        answerability_match = (p_en.answerable == p_ar.answerable)
        expected = set(c_en.expected_provisions)
        top_en = set(p_en.retrieved_alignment_ids[:3])
        top_ar = set(p_ar.retrieved_alignment_ids[:3])
        retrieval_overlap = (
            top_en == top_ar
            and (not expected or (expected & top_en and expected & top_ar))
        )
        claim_ids_match = {claim.claim_id for claim in p_en.claims} == {claim.claim_id for claim in p_ar.claims}
        en_claim_state = {
            (claim.claim_id, claim.supported, bool(set(claim.cited_alignment_ids) & expected))
            for claim in p_en.claims
        }
        ar_claim_state = {
            (claim.claim_id, claim.supported, bool(set(claim.cited_alignment_ids) & expected))
            for claim in p_ar.claims
        }
        claim_state_match = en_claim_state == ar_claim_state

        if jurisdiction_match and answerability_match and retrieval_overlap and claim_ids_match and claim_state_match:
            consistent_pairs += 1

    score = (consistent_pairs / total_pairs) if total_pairs > 0 else 0.0
    return DimensionScore(
        dimension="bilingual_consistency",
        score=score,
        sample_count=total_pairs,
        measured=True if total_pairs > 0 else False,
        details={"consistent_pairs": consistent_pairs, "total_pairs": total_pairs},
    )


def compute_abstention_metrics(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Measure Precision, Recall, and F1 for unanswerable and adversarial cases."""
    case_map = {c.case_id: c for c in cases}
    tp = 0  # Expected unanswerable, predicted unanswerable (correct abstention)
    fp = 0  # Expected answerable, predicted unanswerable (over-abstention)
    fn = 0  # Expected unanswerable, predicted answerable (hallucination)
    tn = 0  # Expected answerable, predicted answerable (correct answer)

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case:
            continue

        expected_abstain = not case.expected_answerable
        predicted_abstain = not pred.answerable

        if expected_abstain and predicted_abstain:
            tp += 1
        elif not expected_abstain and predicted_abstain:
            fp += 1
        elif expected_abstain and not predicted_abstain:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return DimensionScore(
        dimension="abstention",
        score=f1,
        sample_count=len(predictions),
        measured=True,
        details={
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        },
    )


def compute_temporal_accuracy(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    provisions: list[LegalProvision] | None = None,
) -> DimensionScore:
    """Measure adherence to temporal validity windows for historical/versioned queries."""
    temporal_cases = [c for c in cases if c.category == BenchmarkCategory.TEMPORAL_VERSIONING]

    if not temporal_cases:
        return DimensionScore(
            dimension="temporal",
            score=None,
            sample_count=0,
            measured=False,
        )

    provision_by_alignment: dict[str, list[LegalProvision]] = {}
    for provision in provisions or []:
        if provision.alignment_id:
            provision_by_alignment.setdefault(provision.alignment_id, []).append(provision)

    correct = 0
    evaluated = 0
    for case in temporal_cases:
        pred = next((p for p in predictions if p.case_id == case.case_id), None)
        if not pred:
            continue
        evaluated += 1
        if not case.expected_answerable:
            if not pred.answerable:
                correct += 1
            continue
        expected_set = set(case.expected_provisions)
        retrieved_set = set(pred.retrieved_alignment_ids[:5])
        expected_valid = all(
            any(
                (provision.effective_from is None or case.as_of >= provision.effective_from)
                and (provision.effective_until is None or case.as_of <= provision.effective_until)
                for provision in provision_by_alignment.get(alignment_id, [])
            )
            for alignment_id in expected_set
        )
        if expected_valid and expected_set.issubset(retrieved_set):
            correct += 1

    score = (correct / evaluated) if evaluated else 0.0
    return DimensionScore(
        dimension="temporal",
        score=score,
        sample_count=evaluated,
        measured=bool(evaluated),
        details={"correct": correct, "total": evaluated},
    )


def compute_latency_metrics(
    predictions: list[EvaluationPrediction],
) -> DimensionScore:
    """Compute P50, P95, and average latency metrics in milliseconds."""
    if not predictions:
        return DimensionScore(dimension="latency", score=None, sample_count=0, measured=False)

    latencies = sorted(p.latency_ms for p in predictions)
    n = len(latencies)
    p50 = statistics.median(latencies)
    p95 = latencies[0] if n == 1 else statistics.quantiles(latencies, n=100, method="inclusive")[94]
    mean_lat = statistics.mean(latencies)

    # Score from 0.0 to 1.0 (1.0 = <= 50ms, 0.0 = >= 2000ms)
    norm_score = max(0.0, min(1.0, 1.0 - (p95 - 50.0) / 1950.0))

    return DimensionScore(
        dimension="latency",
        score=norm_score,
        sample_count=n,
        measured=True,
        details={
            "p50_ms": p50,
            "p95_ms": p95,
            "mean_ms": mean_lat,
            "min_ms": latencies[0],
            "max_ms": latencies[-1],
        },
    )


def compute_multi_hop_accuracy(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
) -> DimensionScore:
    """Compute complete provision coverage for complex multi-hop scenarios."""
    case_map = {c.case_id: c for c in cases}
    mh_cases = [c for c in cases if c.category == BenchmarkCategory.MULTI_HOP]

    if not mh_cases:
        return DimensionScore(dimension="multi_hop", score=None, sample_count=0, measured=False)

    full_coverage_count = 0
    for case in mh_cases:
        pred = next((p for p in predictions if p.case_id == case.case_id), None)
        if not pred:
            continue
        retrieved_set = set(pred.retrieved_alignment_ids[:5])
        expected_set = set(case.expected_provisions)
        if expected_set.issubset(retrieved_set):
            full_coverage_count += 1

    score = (full_coverage_count / len(mh_cases)) if mh_cases else 0.0
    return DimensionScore(
        dimension="multi_hop",
        score=score,
        sample_count=len(mh_cases),
        measured=True,
        details={"full_coverage_count": full_coverage_count, "total_mh_cases": len(mh_cases)},
    )


def compute_shubhi_signal(
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    corpus_version: str,
    benchmark_version: str = "1.0.0",
    custom_weights: dict[str, float] | None = None,
    valid_corpus_alignment_ids: set[str] | None = None,
    provisions: list[LegalProvision] | None = None,
) -> ShubhiSignalScore:
    """Aggregate all evaluated dimensions into a composite Shubhi Signal scorecard."""
    weights = dict(custom_weights or DEFAULT_DIMENSION_WEIGHTS)
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("Shubhi Signal weights cannot be negative")
    if not any(weights.values()):
        raise ValueError("At least one Shubhi Signal weight must be positive")

    dim_scores: list[DimensionScore] = [
        compute_jurisdiction_accuracy(predictions, cases),
        compute_retrieval_metrics(predictions, cases),
        compute_grounding_metrics(predictions, cases),
        compute_citation_metrics(predictions, cases, valid_corpus_alignment_ids),
        compute_tool_accuracy(predictions, cases),
        compute_bilingual_consistency(predictions, cases),
        compute_abstention_metrics(predictions, cases),
        compute_temporal_accuracy(predictions, cases, provisions),
        compute_multi_hop_accuracy(predictions, cases),
        compute_latency_metrics(predictions),
    ]

    # Calculate weighted aggregate score across measured dimensions
    weighted_sum = 0.0
    weight_total = 0.0

    for ds in dim_scores:
        if ds.measured and ds.score is not None and ds.dimension in weights:
            w = weights[ds.dimension]
            weighted_sum += ds.score * w
            weight_total += w

    aggregate_score = (weighted_sum / weight_total) if weight_total > 0 else None

    # Per-category score breakdown (retrieval hitrate / accuracy)
    category_scores: dict[str, float] = {}
    pred_map = {p.case_id: p for p in predictions}

    for cat in BenchmarkCategory:
        cat_cases = [c for c in cases if c.category == cat]
        if not cat_cases:
            continue
        cat_correct = 0
        for c in cat_cases:
            p = pred_map.get(c.case_id)
            if not p:
                continue
            if _case_passed(c, p, valid_corpus_alignment_ids):
                cat_correct += 1
        category_scores[cat.value] = cat_correct / len(cat_cases)

    # Per-language score breakdown
    language_scores: dict[str, float] = {}
    for lang in (Language.EN, Language.AR, Language.MIXED):
        lang_cases = [c for c in cases if c.language == lang]
        if not lang_cases:
            continue
        lang_correct = 0
        for c in lang_cases:
            p = pred_map.get(c.case_id)
            if not p:
                continue
            if _case_passed(c, p, valid_corpus_alignment_ids):
                lang_correct += 1
        language_scores[lang.value] = lang_correct / len(lang_cases)

    return ShubhiSignalScore(
        benchmark_version=benchmark_version,
        corpus_version=corpus_version,
        dimensions=dim_scores,
        aggregate_score=aggregate_score,
        weights=weights,
        category_scores=category_scores,
        language_scores=language_scores,
    )


def _case_passed(
    case: BenchmarkCase,
    prediction: EvaluationPrediction,
    valid_corpus_alignment_ids: set[str] | None = None,
) -> bool:
    """Return a conservative case-level pass for category/language summaries."""
    if prediction.jurisdiction != case.expected_jurisdiction:
        return False
    if prediction.answerable != case.expected_answerable:
        return False
    cited = set(prediction.cited_alignment_ids)
    if valid_corpus_alignment_ids is not None and not cited <= valid_corpus_alignment_ids:
        return False
    if case.expected_provisions and not set(case.expected_provisions) & set(prediction.retrieved_alignment_ids[:5]):
        return False
    if case.category == BenchmarkCategory.CITATION_VERIFICATION:
        if not set(case.expected_provisions) & cited:
            return False
    if case.expected_tool:
        if prediction.tool_name != case.expected_tool.tool_name:
            return False
        if prediction.tool_arguments != case.expected_tool.arguments:
            return False
        if case.expected_tool.calculation and prediction.calculated_amount is not None:
            if abs(prediction.calculated_amount - case.expected_tool.calculation.expected_amount) > case.expected_tool.calculation.tolerance:
                return False
    return True
