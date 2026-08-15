"""Benchmark reporting module generating structured JSON and Markdown scorecards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import BenchmarkCase, EvaluationPrediction, ShubhiSignalScore


def generate_json_report(
    score: ShubhiSignalScore,
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    valid_alignment_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Generate machine-readable JSON evaluation report."""
    case_map = {c.case_id: c for c in cases}
    failures = []

    for pred in predictions:
        case = case_map.get(pred.case_id)
        if not case:
            continue
        reasons: list[str] = []

        if pred.jurisdiction != case.expected_jurisdiction:
            reasons.append(f"Jurisdiction mismatch: got '{pred.jurisdiction.value}', expected '{case.expected_jurisdiction.value}'")

        if case.expected_answerable:
            if not pred.answerable:
                reasons.append("Unwarranted abstention on valid question")
            elif case.expected_provisions and not (set(pred.retrieved_alignment_ids[:5]) & set(case.expected_provisions)):
                reasons.append(f"Missed expected provisions: expected {case.expected_provisions}, got {pred.retrieved_alignment_ids[:5]}")
        else:
            if pred.answerable:
                reasons.append(f"Failed to abstain: expected abstention due to '{case.abstention_reason}'")

        expected_citations = set(case.expected_provisions)
        actual_citations = set(pred.cited_alignment_ids)
        if valid_alignment_ids is not None:
            invalid = actual_citations - valid_alignment_ids
            if invalid:
                reasons.append(f"Invalid citations: {sorted(invalid)}")
        if case.category.value == "citation_verification" and expected_citations:
            if not expected_citations & actual_citations:
                reasons.append("Citation verification failed: no expected provision was cited")

        if case.expected_tool:
            expected_tool = case.expected_tool
            if pred.tool_name != expected_tool.tool_name:
                reasons.append(f"Tool mismatch: got '{pred.tool_name}', expected '{expected_tool.tool_name}'")
            if pred.tool_arguments != expected_tool.arguments:
                reasons.append("Tool argument mismatch")
            if expected_tool.calculation and pred.calculated_amount is not None:
                if abs(pred.calculated_amount - expected_tool.calculation.expected_amount) > expected_tool.calculation.tolerance:
                    reasons.append("Tool calculation mismatch")
            elif expected_tool.calculation:
                reasons.append("Tool calculation result missing")

        if case.expected_claims:
            predicted_claims = {claim.claim_id: claim for claim in pred.claims}
            for expected_claim in case.expected_claims:
                predicted_claim = predicted_claims.get(expected_claim.claim_id)
                if not predicted_claim:
                    reasons.append(f"Missing predicted claim '{expected_claim.claim_id}'")
                elif predicted_claim.supported is False:
                    reasons.append(f"Unsupported predicted claim '{expected_claim.claim_id}'")
                elif case.expected_provisions and not set(predicted_claim.cited_alignment_ids) & expected_citations:
                    reasons.append(f"Ungrounded predicted claim '{expected_claim.claim_id}'")

        if case.category.value == "temporal_versioning" and case.expected_answerable:
            if not expected_citations & set(pred.retrieved_alignment_ids[:5]):
                reasons.append("Temporal retrieval failed for the requested as-of date")

        if reasons:
            failures.append({
                "case_id": case.case_id,
                "category": case.category.value,
                "language": case.language.value,
                "query": case.query,
                "reasons": reasons,
                "latency_ms": pred.latency_ms,
            })

    return {
        "benchmark_version": score.benchmark_version,
        "corpus_version": score.corpus_version,
        "aggregate_score": score.aggregate_score,
        "weights": score.weights,
        "dimensions": [d.model_dump() for d in score.dimensions],
        "category_scores": score.category_scores,
        "language_scores": score.language_scores,
        "total_cases": len(cases),
        "total_failures": len(failures),
        "failures": failures,
        "latency": next((d.details for d in score.dimensions if d.dimension == "latency"), {}),
    }


def generate_markdown_report(
    score: ShubhiSignalScore,
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    valid_alignment_ids: set[str] | None = None,
) -> str:
    """Generate human-readable Markdown evaluation scorecard."""
    json_data = generate_json_report(score, predictions, cases, valid_alignment_ids)

    lines = [
        "# UAE-LexAgent Benchmark Evaluation Report",
        "",
        f"**Benchmark Version**: `{score.benchmark_version}`  ",
        f"**Corpus Version**: `{score.corpus_version}`  ",
        f"**Aggregate Shubhi Signal Score**: `{score.aggregate_score * 100:.2f}%`" if score.aggregate_score is not None else "**Aggregate Shubhi Signal Score**: `N/A`",
        "",
        "## 1. Dimension Scorecard",
        "",
        "| Dimension | Score | Measured | Sample Count | Key Metrics |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for d in score.dimensions:
        score_str = f"{d.score * 100:.2f}%" if d.score is not None else "N/A"
        measured_str = "Yes" if d.measured else "No"
        details_summary = ", ".join(f"{k}: {v:.3f}" if isinstance(v, float) else f"{k}: {v}" for k, v in d.details.items() if k != "note")
        lines.append(f"| `{d.dimension}` | **{score_str}** | {measured_str} | {d.sample_count} | {details_summary} |")

    lines.extend([
        "",
        "## 2. Category Performance",
        "",
        "| Category | Case Pass Rate | Sample Count |",
        "| :--- | :--- | :--- |",
    ])

    for cat, cat_score in score.category_scores.items():
        cat_count = sum(1 for c in cases if c.category.value == cat)
        lines.append(f"| `{cat}` | {cat_score * 100:.2f}% | {cat_count} |")

    lines.extend([
        "",
        "## 3. Language Performance",
        "",
        "| Language | Case Pass Rate | Sample Count |",
        "| :--- | :--- | :--- |",
    ])

    for lang, lang_score in score.language_scores.items():
        lang_count = sum(1 for c in cases if c.language.value == lang)
        lines.append(f"| `{lang}` | {lang_score * 100:.2f}% | {lang_count} |")

    lines.extend([
        "",
        f"## 4. Failure Summary ({json_data['total_failures']} cases)",
        "",
    ])

    if json_data["failures"]:
        for idx, fail in enumerate(json_data["failures"][:10], 1):
            lines.append(f"### {idx}. Case `{fail['case_id']}` (`{fail['category']}`)")
            lines.append(f"> **Query**: {fail['query']}")
            lines.append(f"- **Issues**: {'; '.join(fail['reasons'])}")
            lines.append("")
        if len(json_data["failures"]) > 10:
            lines.append(f"*... and {len(json_data['failures']) - 10} more failures recorded in JSON report.*")
    else:
        lines.append("No failures recorded! 100% benchmark compliance.")

    return "\n".join(lines)


def save_reports(
    score: ShubhiSignalScore,
    predictions: list[EvaluationPrediction],
    cases: list[BenchmarkCase],
    output_json: Path,
    output_md: Path | None = None,
    valid_alignment_ids: set[str] | None = None,
) -> None:
    """Save both JSON and Markdown reports to disk."""
    output_json.parent.mkdir(parents=True, exist_ok=True)
    json_data = generate_json_report(score, predictions, cases, valid_alignment_ids)
    output_json.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        md_text = generate_markdown_report(score, predictions, cases, valid_alignment_ids)
        output_md.write_text(md_text, encoding="utf-8")
