"""Evaluation and benchmarking module for UAE-LexAgent (Shubhi Signal framework)."""

from .loader import (
    compute_corpus_fingerprint,
    load_benchmark_cases,
    load_benchmark_manifest,
    validate_benchmark_integrity,
    validate_benchmark_manifest,
)
from .metrics import (
    compute_abstention_metrics,
    compute_bilingual_consistency,
    compute_citation_metrics,
    compute_grounding_metrics,
    compute_jurisdiction_accuracy,
    compute_latency_metrics,
    compute_multi_hop_accuracy,
    compute_retrieval_metrics,
    compute_shubhi_signal,
    compute_temporal_accuracy,
    compute_tool_accuracy,
)

__all__ = [
    "compute_abstention_metrics",
    "compute_bilingual_consistency",
    "compute_citation_metrics",
    "compute_grounding_metrics",
    "compute_jurisdiction_accuracy",
    "compute_latency_metrics",
    "compute_multi_hop_accuracy",
    "compute_retrieval_metrics",
    "compute_shubhi_signal",
    "compute_temporal_accuracy",
    "compute_tool_accuracy",
    "load_benchmark_cases",
    "load_benchmark_manifest",
    "compute_corpus_fingerprint",
    "validate_benchmark_integrity",
    "validate_benchmark_manifest",
]
