"""Benchmark evaluator supporting Baseline/Hybrid retrieval and Citation-Grounded Synthesis."""

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
    PredictedClaim,
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
from ..synthesis import (
    CitationVerifier,
    DeterministicTemplateGenerator,
    GeneratorProtocol,
    LegalSynthesisEngine,
    VerificationStatus,
    get_generator,
)
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
    """Evaluator assessing retrieval, synthesis grounding, routing, and tools against the benchmark."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        valid_alignment_ids: set[str],
        retriever_name: str = "baseline",
        generator: GeneratorProtocol | None = None,
        verification_mode: str = "strict",
        provisions: list[LegalProvision] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.retriever = retriever
        self.valid_alignment_ids = valid_alignment_ids
        self.retriever_name = retriever_name
        self.provisions = provisions or []
        self.metadata = dict(metadata or {})
        self.generator = generator or DeterministicTemplateGenerator()
        self.verifier = CitationVerifier(
            valid_corpus_alignment_ids=self.valid_alignment_ids,
            strict=(verification_mode.casefold() == "strict"),
        )
        self.synthesis_engine = LegalSynthesisEngine(
            retriever=self.retriever,
            generator=self.generator,
            verifier=self.verifier,
            provisions=self.provisions,
        )

    def predict_case(self, case: BenchmarkCase) -> EvaluationPrediction:
        """Run routing, retrieval, grounded synthesis, and tools on a benchmark query."""
        start_time = time.perf_counter()

        # 1. Jurisdiction Routing
        decision = classify_jurisdiction(case.query)
        routed_jurisdiction = decision.jurisdiction

        # 2. Complete query context
        legal_query = LegalQuery(
            query_id=case.case_id,
            text=case.query,
            language=case.language,
            jurisdiction=routed_jurisdiction,
            as_of=case.as_of,
        )

        # 3. Grounded Synthesis & Citation Verification Gate
        grounded_answer = self.synthesis_engine.synthesize(legal_query)

        # 4. Extract retrieved alignment IDs
        retrieved_alignment_ids: list[str] = []
        for ev in grounded_answer.referenced_evidence:
            align_id = ev.provision.alignment_id or ev.provision.provision_id
            if align_id not in retrieved_alignment_ids:
                retrieved_alignment_ids.append(align_id)

        # 5. Extract verified cited alignment IDs
        cited_alignment_ids: list[str] = []
        for c in grounded_answer.claims:
            if c.verification_status == VerificationStatus.VERIFIED:
                for cid in c.cited_alignment_ids:
                    if cid not in cited_alignment_ids:
                        cited_alignment_ids.append(cid)

        # 6. Extract structured claims
        predicted_claims: list[PredictedClaim] = [
            PredictedClaim(
                claim_id=c.claim_id,
                claim_key=c.claim_key,
                conclusion=c.conclusion,
                cited_alignment_ids=c.cited_alignment_ids,
                supported=(c.verification_status == VerificationStatus.VERIFIED),
            )
            for c in grounded_answer.claims
        ]

        # 7. Tool evaluation based on query intent
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

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        return EvaluationPrediction(
            case_id=case.case_id,
            answerable=grounded_answer.answerable,
            jurisdiction=routed_jurisdiction,
            retrieved_alignment_ids=retrieved_alignment_ids,
            cited_alignment_ids=cited_alignment_ids,
            claims=predicted_claims,
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


def run_evaluation(
    benchmark_path: Path,
    output_json: Path,
    output_md: Path | None = None,
    corpus_version: str = "2021-as-amended-2024",
    retriever_type: str = "baseline",
    embedding_type: str = "hash",
    reranker_type: str = "identity",
    generator_type: str = "deterministic",
    verification_mode: str = "strict",
) -> ShubhiSignalScore:
    """Run benchmark evaluation with selected retriever, generator, and verification configuration."""
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
    generator = get_generator(generator_type=generator_type)

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
            retriever=retriever,
            valid_alignment_ids=valid_alignment_ids,
            retriever_name="hybrid",
            generator=generator,
            verification_mode=verification_mode,
            provisions=all_provisions,
            metadata={
                "retriever": "hybrid",
                "embedding_provider": type(embedder).__name__,
                "embedding_model": embedder.model_id,
                "reranker": type(reranker).__name__,
                "generator": type(generator).__name__,
                "verification_mode": verification_mode,
                "corpus_fingerprint": index.corpus_fingerprint,
                "corpus_version": index.corpus_version,
            },
        )
    else:
        retriever = InMemoryRetriever(all_provisions)
        evaluator = BenchmarkEvaluator(
            retriever=retriever,
            valid_alignment_ids=valid_alignment_ids,
            retriever_name="baseline",
            generator=generator,
            verification_mode=verification_mode,
            provisions=all_provisions,
            metadata={
                "retriever": "baseline",
                "generator": type(generator).__name__,
                "verification_mode": verification_mode,
                "corpus_fingerprint": compute_corpus_fingerprint(all_provisions),
                "corpus_version": corpus_version,
            },
        )

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
    parser = argparse.ArgumentParser(description="UAE-LexAgent Benchmark Evaluator (Retrieval + Grounded Synthesis)")
    parser.add_argument("--benchmark", type=Path, default=Path("data/evaluation/benchmark_queries.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_results.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/benchmark_report.md"))
    parser.add_argument("--retriever", choices=["baseline", "hybrid"], default="hybrid")
    parser.add_argument("--embedding", choices=["hash", "sentence-transformer", "openai"], default="hash")
    parser.add_argument("--reranker", choices=["identity", "cross-encoder"], default="identity")
    parser.add_argument("--generator", choices=["deterministic", "openai", "anthropic", "ollama", "vllm"], default="deterministic")
    parser.add_argument("--verification", choices=["strict", "permissive"], default="strict")
    args = parser.parse_args()

    print(
        f"Running Benchmark: retriever={args.retriever}, embedding={args.embedding}, "
        f"generator={args.generator}, verification={args.verification}"
    )
    score = run_evaluation(
        benchmark_path=args.benchmark,
        output_json=args.output,
        output_md=args.report,
        retriever_type=args.retriever,
        embedding_type=args.embedding,
        reranker_type=args.reranker,
        generator_type=args.generator,
        verification_mode=args.verification,
    )
    print("Evaluation Complete!")
    if score.aggregate_score is not None:
        print(f"Aggregate Shubhi Signal Score: {score.aggregate_score * 100:.2f}%")
    print(f"JSON Report: {args.output}")
    print(f"Markdown Report: {args.report}")


if __name__ == "__main__":
    main()
