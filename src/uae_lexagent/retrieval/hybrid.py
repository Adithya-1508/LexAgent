"""Production Hybrid Legal Retriever integrating Dense Search, BM25, RRF, and Reranking."""

from __future__ import annotations

from datetime import date

from ..models import Jurisdiction, Language, LegalEvidence, LegalQuery
from .contracts import RetrievalFilters, matches_retrieval_filters
from .fusion import reciprocal_rank_fusion
from .indexing import RetrievalIndex
from .reranker import IdentityReranker, Reranker
from .sparse import tokenize_legal_text


class HybridLegalRetriever:
    """Bilingual hybrid retriever combining dense vector search, BM25, and rank fusion."""

    def __init__(
        self,
        index: RetrievalIndex,
        reranker: Reranker | None = None,
        rrf_k: int = 60,
        rrf_alpha: float = 0.5,
    ) -> None:
        self.index = index
        self.reranker = reranker or IdentityReranker()
        self.rrf_k = rrf_k
        self.rrf_alpha = rrf_alpha

    def search(
        self,
        query: LegalQuery,
        *,
        top_k: int = 5,
        filters: RetrievalFilters | None = None,
    ) -> list[LegalEvidence]:
        """Execute hybrid search pipeline."""
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        # 1. Effective filters construction
        supplied_filters = filters or RetrievalFilters()
        effective_filters = RetrievalFilters(
            jurisdiction=(
                supplied_filters.jurisdiction
                if supplied_filters.jurisdiction != Jurisdiction.UNKNOWN
                else query.jurisdiction
            ),
            as_of=supplied_filters.as_of or query.as_of,
            language=(
                supplied_filters.language
                if supplied_filters.language != Language.UNKNOWN
                else query.language
            ),
            include_translations=supplied_filters.include_translations,
            authoritative_only=supplied_filters.authoritative_only,
            document_ids=supplied_filters.document_ids,
            legal_domain=supplied_filters.legal_domain,
        )

        # 2. Dense Vector Retrieval
        query_vectors = list(self.index.embedding_provider.embed([query.text]))
        if len(query_vectors) != 1:
            raise ValueError("Embedding provider must return exactly one query vector")
        query_vector = query_vectors[0]
        dense_candidates = self.index.vector_store.query(
            vector=query_vector,
            filters=effective_filters,
            top_k=top_k * 3,
        )

        # 3. BM25 Sparse Retrieval
        query_tokens = tokenize_legal_text(query.text, query.language)
        sparse_candidates = self.index.sparse_index.query(
            query_tokens=query_tokens,
            filters=effective_filters,
            top_k=top_k * 3,
        )

        # 4. Reciprocal Rank Fusion
        fused_candidates = reciprocal_rank_fusion(
            dense_ranks=dense_candidates,
            sparse_ranks=sparse_candidates,
            k=self.rrf_k,
            alpha=self.rrf_alpha,
        )

        # 5. Optional Reranker
        reranked_candidates = self.reranker.rerank(
            query=query,
            candidates=fused_candidates,
            top_k=top_k,
        )

        # 6. Resolve aligned legal evidence without bypassing the effective filters.
        evidence_list: list[LegalEvidence] = []
        for rank, cand in enumerate(reranked_candidates, start=1):
            align_id = cand.alignment_id
            pair = self.index.alignment_map.get(align_id, {})

            eligible_pair = {
                language: provision
                for language, provision in pair.items()
                if matches_retrieval_filters(provision, effective_filters)
            }
            fallback = self.index.provisions_by_id.get(cand.provision_id)
            if fallback and matches_retrieval_filters(fallback, effective_filters):
                eligible_pair.setdefault(fallback.language, fallback)

            if query.language == Language.AR:
                chosen_provision = eligible_pair.get("ar") or eligible_pair.get("en")
            elif query.language == Language.EN:
                chosen_provision = eligible_pair.get("en") or eligible_pair.get("ar")
            else:
                chosen_provision = eligible_pair.get("ar") or eligible_pair.get("en")

            if not chosen_provision:
                continue

            evidence = LegalEvidence(
                evidence_id=f"ev-{chosen_provision.provision_id}",
                provision=chosen_provision,
                dense_score=cand.dense_score,
                sparse_score=cand.sparse_score,
                reranker_score=cand.fused_score,
                retrieval_rank=rank,
                aligned_provisions=[
                    provision
                    for provision in eligible_pair.values()
                    if provision.provision_id != chosen_provision.provision_id
                ],
            )
            evidence_list.append(evidence)

        return evidence_list

    def retrieve(
        self,
        query: str | LegalQuery,
        *,
        top_k: int = 5,
        jurisdiction: Jurisdiction | None = None,
        as_of: date | None = None,
        language: Language = Language.UNKNOWN,
    ) -> list[LegalEvidence]:
        """Convenience method accepting raw strings."""
        if isinstance(query, str):
            legal_query = LegalQuery(
                query_id="adhoc",
                text=query,
                language=language,
                jurisdiction=jurisdiction or Jurisdiction.UNKNOWN,
                as_of=as_of,
            )
        else:
            legal_query = query

        return self.search(legal_query, top_k=top_k)
