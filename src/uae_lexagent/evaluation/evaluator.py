"""Benchmark evaluator supporting both Baseline and Hybrid retrieval backends."""

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
    LegalProvision,
    LegalQuery,
    ShubhiSignalScore,
)
from ..retrieval import (
    HybridLegalRetriever,
    IdentityReranker,
    InMemoryRetriever,
    RetrieverProtocol,
    SentenceTransformerReranker,
    build_retrieval_index,
    get_embedding_provider,
)
from ..routing import classify_jurisdiction
from ..tools import calculate_gratuity
from .loader import (
    compute_corpus_fingerprint,
    load_benchmark_cases,
    load_benchmark_manifest,
    validate_benchmark_integrity,
    validate_benchmark_manifest,
)
from .metrics import compute_shubhi_signal
from .report import save_reports


class BenchmarkEvaluator:
    """Evaluator assessing retrieval, routing, and tools against the Shubhi Signal benchmark."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        valid_alignment_ids: set[str],
        retriever_name: str = "baseline",
        provisions: list[LegalProvision] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.retriever = retriever
        self.valid_alignment_ids = valid_alignment_ids
        self.retriever_name = retriever_name
        self.provisions = provisions or []
        self.metadata = dict(metadata or {})

    def predict_case(self, case: BenchmarkCase) -> EvaluationPrediction:
        """Run routing, retrieval, and tools on a benchmark query."""
        start_time = time.perf_counter()

        # 1. Jurisdiction Routing
        decision = classify_jurisdiction(case.query)
        routed_jurisdiction = decision.jurisdiction

        # 2. Retrieval using the declared protocol and complete query context
        legal_query = LegalQuery(
            query_id=case.case_id,
            text=case.query,
            language=case.language,
            jurisdiction=routed_jurisdiction,
            as_of=case.as_of,
        )
        evidence = self.retriever.search(legal_query, top_k=5)
        retrieved_alignment_ids: list[str] = []
        for ev in evidence:
            if ev.provision.alignment_id and ev.provision.alignment_id not in retrieved_alignment_ids:
                retrieved_alignment_ids.append(ev.provision.alignment_id)

        # 3. Tool evaluation based on query intent
        tool_name = None
        tool_arguments = {}
        calc_amount = None

        salary_match = re.search(
            r"(?:basic\s+salary|salary)\s*(?:is|=|:)?\s*(?:AED\s*)?(\d+(?:\.\d+)?)",
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

        # 4. Answerability heuristic must remain independent of benchmark labels.
        answerable = _is_answerable(case.query, routed_jurisdiction, self.provisions, case.as_of)

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
        """Run benchmark evaluation and return composite score."""
        predictions = [self.predict_case(case) for case in cases]
        score = compute_shubhi_signal(
            predictions=predictions,
            cases=cases,
            corpus_version=corpus_version,
            benchmark_version=benchmark_version,
            valid_corpus_alignment_ids=self.valid_alignment_ids,
            provisions=self.provisions,
            metadata=self.metadata,
        )
        return score, predictions


class BaselineEvaluator(BenchmarkEvaluator):
    """Backward-compatible baseline evaluator facade."""

    def __init__(self, provisions: list[LegalProvision]) -> None:
        super().__init__(
            retriever=InMemoryRetriever(provisions),
            valid_alignment_ids={p.alignment_id for p in provisions if p.alignment_id},
            retriever_name="baseline",
            provisions=provisions,
            metadata={
                "retriever": "baseline",
                "corpus_fingerprint": compute_corpus_fingerprint(provisions),
            },
        )


def _is_answerable(
    query: str,
    jurisdiction: Jurisdiction,
    provisions: list[LegalProvision],
    as_of: date | None = None,
) -> bool:
    """Conservative answerability gate independent of benchmark labels."""
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
    article_matches = re.findall(
        r"(?:article|المادة)\s*\(?\s*(\d+)\s*\)?",
        lowered,
        re.IGNORECASE,
    )
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
    retriever_type: str = "baseline",
    embedding_type: str = "hash",
    reranker_type: str = "identity",
) -> ShubhiSignalScore:
    """Run benchmark evaluation with selected retriever and embedding configuration."""
    project_root = Path(__file__).resolve().parents[3]
    data_dir = project_root / "data" / "official_sources"
    fdl_provisions = load_provisions(data_dir / "fdl_33_2021_provisions.json")
    cab_provisions = load_provisions(data_dir / "cabinet_resolution_1_2022_provisions.json")
    all_provisions = fdl_provisions + cab_provisions

    benchmark_path = benchmark_path if benchmark_path.is_absolute() else project_root / benchmark_path
    cases = load_benchmark_cases(benchmark_path)
    manifest = load_benchmark_manifest(project_root / "data" / "evaluation" / "manifest.json")
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

    valid_alignment_ids = {p.alignment_id for p in all_provisions if p.alignment_id}

    retriever_type_clean = retriever_type.casefold().strip()
    if retriever_type_clean not in {"baseline", "hybrid"}:
        raise ValueError(f"Unknown retriever type: '{retriever_type}'")
    reranker_type_clean = reranker_type.casefold().strip()
    if reranker_type_clean not in {"identity", "cross-encoder"}:
        raise ValueError(f"Unknown reranker type: '{reranker_type}'")

    if retriever_type_clean == "hybrid":
        embedder = get_embedding_provider(provider_type=embedding_type)
        index = build_retrieval_index(
            provisions=all_provisions,
            embedding_provider=embedder,
            corpus_version=corpus_version,
        )
        reranker = SentenceTransformerReranker() if reranker_type_clean == "cross-encoder" else IdentityReranker()
        retriever = HybridLegalRetriever(index=index, reranker=reranker)
        evaluator = BenchmarkEvaluator(
            retriever,
            valid_alignment_ids=valid_alignment_ids,
            retriever_name="hybrid",
            provisions=all_provisions,
            metadata={
                "retriever": "hybrid",
                "embedding_provider": type(embedder).__name__,
                "embedding_model": embedder.model_id,
                "reranker": type(reranker).__name__,
                "corpus_fingerprint": index.corpus_fingerprint,
                "corpus_version": index.corpus_version,
            },
        )
    else:
        retriever = InMemoryRetriever(all_provisions)
        evaluator = BaselineEvaluator(all_provisions)

    score, predictions = evaluator.evaluate(
        cases,
        corpus_version=corpus_version,
        benchmark_version=manifest.benchmark_version,
    )

    save_reports(
        score=score,
        predictions=predictions,
        cases=cases,
        output_json=output_json,
        output_md=output_md,
        valid_alignment_ids=valid_alignment_ids,
    )

    return score


def main() -> None:
    parser = argparse.ArgumentParser(description="UAE-LexAgent Benchmark Evaluator (Baseline vs Hybrid)")
    parser.add_argument("--benchmark", type=Path, default=Path("data/evaluation/benchmark_queries.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_results.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/benchmark_report.md"))
    parser.add_argument("--retriever", choices=["baseline", "hybrid"], default="hybrid")
    parser.add_argument("--embedding", choices=["hash", "sentence-transformer", "openai"], default="hash")
    parser.add_argument("--reranker", choices=["identity", "cross-encoder"], default="identity")
    args = parser.parse_args()

    print(f"Running Benchmark with: retriever={args.retriever}, embedding={args.embedding}, reranker={args.reranker}")
    score = run_evaluation(
        benchmark_path=args.benchmark,
        output_json=args.output,
        output_md=args.report,
        retriever_type=args.retriever,
        embedding_type=args.embedding,
        reranker_type=args.reranker,
    )
    print("Evaluation Complete!")
    if score.aggregate_score is not None:
        print(f"Aggregate Shubhi Signal Score: {score.aggregate_score * 100:.2f}%")
    print(f"JSON Report: {args.output}")
    print(f"Markdown Report: {args.report}")


if __name__ == "__main__":
    main()
