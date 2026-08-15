"""Cross-encoder and identity rerankers for post-fusion candidate scoring."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..models import LegalQuery
from .contracts import RankedCandidate


class Reranker(Protocol):
    """Protocol for post-fusion candidate rerankers."""

    def rerank(
        self,
        query: LegalQuery,
        candidates: Sequence[RankedCandidate],
        top_k: int = 5,
    ) -> list[RankedCandidate]: ...


class IdentityReranker:
    """Deterministic offline reranker that preserves RRF rank order."""

    def rerank(
        self,
        query: LegalQuery,
        candidates: Sequence[RankedCandidate],
        top_k: int = 5,
    ) -> list[RankedCandidate]:
        return list(candidates[:top_k])


class SentenceTransformerReranker:
    """Multilingual cross-encoder reranker (lazy-loaded)."""

    def __init__(self, model_id: str = "BAAI/bge-reranker-base") -> None:
        self.model_id = model_id
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self.model_id)
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is not installed. Install with `pip install sentence-transformers`"
                ) from exc

    def rerank(
        self,
        query: LegalQuery,
        candidates: Sequence[RankedCandidate],
        top_k: int = 5,
    ) -> list[RankedCandidate]:
        if not candidates:
            return []
        self._load()

        pairs = [(query.text, c.provision.text if c.provision else "") for c in candidates]
        scores = self._model.predict(pairs)

        scored_candidates = []
        for cand, score in zip(candidates, scores):
            updated = cand.model_copy(update={"fused_score": float(score)})
            scored_candidates.append(updated)

        scored_candidates.sort(key=lambda x: (-x.fused_score, x.alignment_id))
        return scored_candidates[:top_k]
