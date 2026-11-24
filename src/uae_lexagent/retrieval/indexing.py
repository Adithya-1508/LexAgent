"""Indexing engine to build synchronized dense and sparse legal provision indices."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Sequence

from ..ingestion import compute_corpus_fingerprint
from ..models import LegalProvision
from .contracts import IndexedProvision
from .embeddings import EmbeddingProvider
from .sparse import BM25Index, tokenize_legal_text
from .vector_store import InMemoryVectorStore, VectorStore


class RetrievalIndex:
    """Synchronized container holding dense and sparse indices with bilingual mapping."""

    def __init__(
        self,
        vector_store: VectorStore,
        sparse_index: BM25Index,
        provisions: Sequence[LegalProvision],
        embedding_provider: EmbeddingProvider,
        corpus_fingerprint: str,
        corpus_version: str = "2021-as-amended-2024",
    ) -> None:
        self.vector_store = vector_store
        self.sparse_index = sparse_index
        self.embedding_provider = embedding_provider
        self.corpus_fingerprint = corpus_fingerprint
        self.corpus_version = corpus_version
        self.created_at = datetime.utcnow()

        self.provisions_by_id: dict[str, LegalProvision] = {p.provision_id: p for p in provisions}
        self.alignment_map: dict[str, dict[str, LegalProvision]] = {}
        for p in provisions:
            if p.alignment_id:
                pair = self.alignment_map.setdefault(p.alignment_id, {})
                if p.language in pair:
                    raise ValueError(
                        f"Duplicate {p.language} provision for alignment_id '{p.alignment_id}'"
                    )
                pair[p.language] = p


def build_retrieval_index(
    provisions: Sequence[LegalProvision],
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore | None = None,
    corpus_version: str = "2021-as-amended-2024",
) -> RetrievalIndex:
    """Construct synchronized dense and sparse index from provisions."""
    provision_list = list(provisions)
    if not provision_list:
        raise ValueError("Cannot build index from empty provision list")

    store = vector_store or InMemoryVectorStore(dimension=embedding_provider.dimension)
    bm25 = BM25Index()

    # Verify unique IDs
    seen_ids = set()
    for p in provision_list:
        if p.provision_id in seen_ids:
            raise ValueError(f"Duplicate provision_id '{p.provision_id}' in index build")
        seen_ids.add(p.provision_id)

    # 1. Compute Embeddings & Prepare IndexedProvisions
    texts = [p.text for p in provision_list]
    vectors = list(embedding_provider.embed(texts))
    if len(vectors) != len(provision_list):
        raise ValueError(
            f"Embedding provider returned {len(vectors)} vectors for {len(provision_list)} provisions"
        )

    indexed_records: list[IndexedProvision] = []
    expected_dimension = embedding_provider.dimension
    for prov, vec in zip(provision_list, vectors):
        if len(vec) != expected_dimension:
            raise ValueError(
                f"Embedding dimension mismatch for '{prov.provision_id}': "
                f"expected {expected_dimension}, got {len(vec)}"
            )
        if not all(math.isfinite(value) for value in vec):
            raise ValueError(f"Embedding for '{prov.provision_id}' contains non-finite values")
        tokens = tokenize_legal_text(prov.text, prov.language)
        indexed_records.append(
            IndexedProvision(
                provision=prov,
                tokens=tokens,
                vector=vec,
            )
        )

    # 2. Populate Stores
    store.upsert(indexed_records)
    bm25.build(provision_list)

    return RetrievalIndex(
        vector_store=store,
        sparse_index=bm25,
        provisions=provision_list,
        embedding_provider=embedding_provider,
        corpus_fingerprint=compute_corpus_fingerprint(provision_list, legacy_compatible=True),
        corpus_version=corpus_version,
    )
