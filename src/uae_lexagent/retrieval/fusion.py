"""Reciprocal Rank Fusion (RRF) for combining dense and sparse rankings."""

from __future__ import annotations

from typing import Sequence

from .contracts import RankedCandidate


def reciprocal_rank_fusion(
    dense_ranks: Sequence[RankedCandidate],
    sparse_ranks: Sequence[RankedCandidate],
    *,
    k: int = 60,
    alpha: float = 0.5,
) -> list[RankedCandidate]:
    """Fuse dense and sparse candidate rankings using canonical alignment IDs."""
    if k <= 0:
        raise ValueError("k must be positive")
    if not (0.0 <= alpha <= 1.0):
        raise ValueError("alpha must be between 0.0 and 1.0")

    # Map alignment_id -> {dense_rank, dense_score, sparse_rank, sparse_score, candidate}
    candidates_by_align: dict[str, dict] = {}

    for cand in dense_ranks:
        align_id = cand.alignment_id
        if align_id not in candidates_by_align:
            candidates_by_align[align_id] = {
                "alignment_id": align_id,
                "provision_id": cand.provision_id,
                "dense_rank": cand.dense_rank,
                "dense_score": cand.dense_score,
                "sparse_rank": None,
                "sparse_score": None,
                "provision": cand.provision,
            }
        else:
            if candidates_by_align[align_id]["dense_rank"] is None:
                candidates_by_align[align_id]["dense_rank"] = cand.dense_rank
                candidates_by_align[align_id]["dense_score"] = cand.dense_score

    for cand in sparse_ranks:
        align_id = cand.alignment_id
        if align_id not in candidates_by_align:
            candidates_by_align[align_id] = {
                "alignment_id": align_id,
                "provision_id": cand.provision_id,
                "dense_rank": None,
                "dense_score": None,
                "sparse_rank": cand.sparse_rank,
                "sparse_score": cand.sparse_score,
                "provision": cand.provision,
            }
        else:
            if candidates_by_align[align_id]["sparse_rank"] is None:
                candidates_by_align[align_id]["sparse_rank"] = cand.sparse_rank
                candidates_by_align[align_id]["sparse_score"] = cand.sparse_score

    fused_list: list[RankedCandidate] = []

    for align_id, data in candidates_by_align.items():
        dense_contrib = (
            (alpha / (k + data["dense_rank"]))
            if data["dense_rank"] is not None
            else 0.0
        )
        sparse_contrib = (
            ((1.0 - alpha) / (k + data["sparse_rank"]))
            if data["sparse_rank"] is not None
            else 0.0
        )
        fused_score = dense_contrib + sparse_contrib

        fused_list.append(
            RankedCandidate(
                alignment_id=align_id,
                provision_id=data["provision_id"],
                dense_rank=data["dense_rank"],
                dense_score=data["dense_score"],
                sparse_rank=data["sparse_rank"],
                sparse_score=data["sparse_score"],
                fused_score=fused_score,
                provision=data["provision"],
            )
        )

    # Deterministic sorting: highest score first, then alphabetical alignment_id
    fused_list.sort(key=lambda x: (-x.fused_score, x.alignment_id))
    return fused_list
