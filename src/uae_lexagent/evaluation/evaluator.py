"""Baseline benchmark evaluator running predictions against retrieval and routing engines."""

from __future__ import annotations

import argparse
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..ingestion import load_provisions
from ..models import (
    BenchmarkCase,
    EvaluationPrediction,
    Jurisdiction,
    Language,
    LegalProvision,
    LegalQuery,
    ShubhiSignalScore,
)
from ..retrieval import InMemoryRetriever
from ..routing import classify_jurisdiction
from ..tools import calculate_gratuity
from .loader import (
    load_benchmark_cases,
    load_benchmark_manifest,
    validate_benchmark_integrity,
    validate_benchmark_manifest,
)
from .metrics import compute_shubhi_signal
from .report import save_reports


class BaselineEvaluator:
    """Evaluator evaluating baseline in-memory lexical retrieval, jurisdiction routing, and tools."""

    def __init__(self, provisions: list[LegalProvision]) -> None:
        self.provisions = provisions
        self.retriever = InMemoryRetriever(provisions)
        self.valid_alignment_ids = {p.alignment_id for p in provisions if p.alignment_id}

    def predict_case(self, case: BenchmarkCase) -> EvaluationPrediction:
        """Run routing, retrieval, and optional tools on a single benchmark query."""
        start_time = time.perf_counter()

        # 1. Jurisdiction Routing
        decision = classify_jurisdiction(case.query)
        routed_jurisdiction = decision.jurisdiction

        # 2. Retrieval must receive the same routing, language, and temporal context
        # that a production pipeline would use.
        query = LegalQuery(
            query_id=case.case_id,
            text=case.query,
            language=case.language,
            jurisdiction=routed_jurisdiction,
            as_of=case.as_of,
        )
        evidence = self.retriever.search(
            query,
            top_k=5,
            jurisdiction=routed_jurisdiction,
        )
        retrieved_alignment_ids: list[str] = []
        for ev in evidence:
            if ev.provision.alignment_id and ev.provision.alignment_id not in retrieved_alignment_ids:
                retrieved_alignment_ids.append(ev.provision.alignment_id)

        # 3. Tool evaluation if query has basic salary and service years
        tool_name = None
        tool_arguments = {}
        calc_amount = None

        if _looks_like_calculation(case.query):
            salary_match = re.search(
                r"(?:basic\s+salary|salary)\s*(?:is|=|:)??\s*(?:AED\s*)?(\d+(?:\.\d+)?)",
                case.query,
                re.IGNORECASE,
            )
            years_match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:years?\s+of\s+service|years?\s+service)",
                case.query,
                re.IGNORECASE,
            )
            if salary_match and years_match:
                tool_name = "calculate_gratuity"
                salary = Decimal(salary_match.group(1))
                years = Decimal(years_match.group(1))
                tool_arguments = {"basic_salary": float(salary), "service_years": float(years)}
                try:
                    res = calculate_gratuity(salary, years)
                    calc_amount = Decimal(str(res["total_gratuity"]))
                except Exception:
                    pass

        # 4. Answerability heuristic: abstain if routed jurisdiction is foreign or adversarial non-existent article
        answerable = _is_answerable(case.query, routed_jurisdiction, self.provisions)

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        return EvaluationPrediction(
            case_id=case.case_id,
            answerable=answerable,
            jurisdiction=routed_jurisdiction,
            retrieved_alignment_ids=retrieved_alignment_ids,
            cited_alignment_ids=retrieved_alignment_ids[:2] if answerable else [],
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            calculated_amount=calc_amount,
            latency_ms=latency_ms,
        )

    def evaluate(
        self,
        cases: list[BenchmarkCase],
        corpus_version: str,
        benchmark_version: str = "1.0.0",
    ) -> tuple[ShubhiSignalScore, list[EvaluationPrediction]]:
        """Run all test cases and compute Shubhi Signal scores."""
        predictions = [self.predict_case(case) for case in cases]
        score = compute_shubhi_signal(
            predictions=predictions,
            cases=cases,
            corpus_version=corpus_version,
            benchmark_version=benchmark_version,
            valid_corpus_alignment_ids=self.valid_alignment_ids,
            provisions=self.provisions,
        )
        return score, predictions


def _looks_like_calculation(query: str) -> bool:
    lowered = query.casefold()
    return any(
        marker in lowered
        for marker in ("calculate", "calculation", "gratuity", "end-of-service", "مكافأة", "حساب")
    )


def _is_answerable(
    query: str,
    jurisdiction: Jurisdiction,
    provisions: list[LegalProvision],
    as_of: date | None = None,
) -> bool:
    """Conservative baseline answerability gate independent of benchmark labels."""
    if jurisdiction is not Jurisdiction.UAE_ONSHORE:
        return False

    lowered = query.casefold()
    out_of_scope_terms = (
        "domestic worker",
        "domestic workers",
        "federal civil servant",
        "federal government employee",
        "criminal offense",
        "criminal offence",
        "العمالة المساعدة",
        "موظف حكومي",
        "الجريمة",
    )
    if any(term in lowered for term in out_of_scope_terms):
        return False

    article_numbers = {
        provision.article
        for provision in provisions
        if provision.jurisdiction is Jurisdiction.UAE_ONSHORE
    }
    article_matches = re.findall(r"(?:article|المادة)\s*\(?\s*(\d+)\s*\)?", lowered, re.IGNORECASE)
    if article_matches and any(number not in article_numbers for number in article_matches):
        return False

    if as_of is not None:
        valid_provisions = [
            provision
            for provision in provisions
            if provision.jurisdiction is Jurisdiction.UAE_ONSHORE
            and (provision.effective_from is None or as_of >= provision.effective_from)
            and (provision.effective_until is None or as_of <= provision.effective_until)
        ]
        if not valid_provisions:
            return False

    return True


def run_evaluation(
    benchmark_path: Path,
    output_json: Path,
    output_md: Path | None = None,
    corpus_version: str = "2021-as-amended-2024",
) -> ShubhiSignalScore:
    """CLI and API runner loading corpus, validating benchmark, and generating reports."""
    project_root = Path(__file__).resolve().parents[3]
    data_dir = project_root / "data" / "official_sources"
    fdl_provisions = load_provisions(data_dir / "fdl_33_2021_provisions.json")
    cab_provisions = load_provisions(data_dir / "cabinet_resolution_1_2022_provisions.json")
    all_provisions = fdl_provisions + cab_provisions

    benchmark_path = benchmark_path if benchmark_path.is_absolute() else project_root / benchmark_path
    cases = load_benchmark_cases(benchmark_path)
    manifest_path = project_root / "data" / "evaluation" / "manifest.json"
    manifest = load_benchmark_manifest(manifest_path)
    issues = validate_benchmark_manifest(
        manifest,
        benchmark_path,
        cases,
        all_provisions,
        corpus_version=corpus_version,
    )
    issues.extend(validate_benchmark_integrity(cases, all_provisions, corpus_version=corpus_version))
    if issues:
        raise ValueError(f"Benchmark integrity validation failed with {len(issues)} issues:\n" + "\n".join(issues))

    evaluator = BaselineEvaluator(all_provisions)
    score, predictions = evaluator.evaluate(
        cases,
        corpus_version=corpus_version,
        benchmark_version=manifest.benchmark_version,
    )

    # Inputs are repository-owned; relative outputs belong to the caller's cwd.
    output_json = output_json if output_json.is_absolute() else Path.cwd() / output_json
    if output_md is not None:
        output_md = output_md if output_md.is_absolute() else Path.cwd() / output_md

    save_reports(
        score=score,
        predictions=predictions,
        cases=cases,
        output_json=output_json,
        output_md=output_md,
        valid_alignment_ids=evaluator.valid_alignment_ids,
    )

    return score


def main() -> None:
    parser = argparse.ArgumentParser(description="UAE-LexAgent Shubhi Signal Benchmark Runner")
    parser.add_argument("--benchmark", type=Path, default=Path("data/evaluation/benchmark_queries.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_results.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/benchmark_report.md"))
    args = parser.parse_args()

    print(f"Loading benchmark from: {args.benchmark}")
    score = run_evaluation(args.benchmark, args.output, args.report)
    print(f"Evaluation Complete!")
    if score.aggregate_score is not None:
        print(f"Aggregate Shubhi Signal Score: {score.aggregate_score * 100:.2f}%")
    print(f"JSON Report written to: {args.output}")
    print(f"Markdown Report written to: {args.report}")


if __name__ == "__main__":
    main()
