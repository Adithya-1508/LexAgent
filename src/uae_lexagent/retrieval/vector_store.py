"""Vector store implementations for dense semantic retrieval."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from ..models import Jurisdiction, Language, LegalProvision
from .contracts import IndexedProvision, RankedCandidate, RetrievalFilters, matches_retrieval_filters

_OPEN_ENDED_FROM = 1
_OPEN_ENDED_UNTIL = 999999999


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _is_temporally_valid(provision: LegalProvision, as_of: date | None) -> bool:
    if as_of is None:
        return True
    if provision.effective_from is not None and as_of < provision.effective_from:
        return False
    if provision.effective_until is not None and as_of > provision.effective_until:
        return False
    return True


class VectorStore(Protocol):
    """Protocol for vector stores supporting filtered similarity queries."""

    def upsert(self, records: Sequence[IndexedProvision]) -> None: ...

    def query(
        self,
        vector: Sequence[float],
        filters: RetrievalFilters | None = None,
        top_k: int = 5,
    ) -> list[RankedCandidate]: ...

    def delete(self, provision_ids: Sequence[str]) -> None: ...


class InMemoryVectorStore:
    """Pure-Python in-memory vector store with hard metadata pre-filtering."""

    def __init__(self, dimension: int | None = None) -> None:
        self._dimension = dimension
        self._records: dict[str, IndexedProvision] = {}

    def upsert(self, records: Sequence[IndexedProvision]) -> None:
        """Add or update indexed provisions."""
        for rec in records:
            if rec.vector is None:
                raise ValueError(f"IndexedProvision '{rec.provision_id}' lacks embedding vector")
            if self._dimension is None:
                self._dimension = len(rec.vector)
            elif len(rec.vector) != self._dimension:
                raise ValueError(
                    f"Vector dimension mismatch for '{rec.provision_id}': "
                    f"expected {self._dimension}, got {len(rec.vector)}"
                )
            self._records[rec.provision_id] = rec

    def query(
        self,
        vector: Sequence[float],
        filters: RetrievalFilters | None = None,
        top_k: int = 5,
    ) -> list[RankedCandidate]:
        """Find top-K candidate provisions using cosine similarity with metadata pre-filtering."""
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self._records or not vector:
            return []
        if self._dimension is not None and len(vector) != self._dimension:
            raise ValueError(
                f"Query vector dimension mismatch: expected {self._dimension}, got {len(vector)}"
            )

        scored: list[tuple[float, IndexedProvision]] = []

        for rec in self._records.values():
            prov = rec.provision

            if not matches_retrieval_filters(prov, filters):
                continue

            sim = _cosine_similarity(vector, rec.vector or [])
            scored.append((sim, rec))

        # Deterministic sorting
        scored.sort(
            key=lambda x: (
                -x[0],
                x[1].alignment_id or "",
                x[1].provision_id,
            )
        )

        candidates: list[RankedCandidate] = []
        for rank, (score, rec) in enumerate(scored[:top_k], start=1):
            candidates.append(
                RankedCandidate(
                    alignment_id=rec.alignment_id or rec.provision_id,
                    provision_id=rec.provision_id,
                    dense_rank=rank,
                    dense_score=score,
                    fused_score=score,
                    provision=rec.provision,
                )
            )

        return candidates

    def delete(self, provision_ids: Sequence[str]) -> None:
        """Remove provisions from vector index."""
        for pid in provision_ids:
            self._records.pop(pid, None)


class QdrantVectorStore:
    """Optional vector store adapter for Qdrant (lazy-loaded)."""

    def __init__(
        self,
        collection_name: str = "uae_legal_provisions",
        location: str = ":memory:",
        dimension: int = 128,
    ) -> None:
        self.collection_name = collection_name
        self.location = location
        self.dimension = dimension
        self._client = None

    def _load(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.http import models

                self._client = QdrantClient(location=self.location)
                collections = [c.name for c in self._client.get_collections().collections]
                if self.collection_name not in collections:
                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=models.VectorParams(
                            size=self.dimension,
                            distance=models.Distance.COSINE,
                        ),
                    )
            except ImportError as exc:
                raise ImportError(
                    "qdrant-client is not installed. Install with `pip install qdrant-client`"
                ) from exc
        return self._client

    def upsert(self, records: Sequence[IndexedProvision]) -> None:
        client = self._load()
        from qdrant_client.http import models

        points = []
        for rec in records:
            if rec.vector is None or len(rec.vector) != self.dimension:
                raise ValueError(
                    f"Vector dimension mismatch for '{rec.provision_id}': expected {self.dimension}"
                )
            point_id = str(uuid5(NAMESPACE_URL, f"uae-lexagent:{rec.provision_id}"))
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=rec.vector or [],
                    payload={
                        "provision_id": rec.provision_id,
                        "alignment_id": rec.alignment_id,
                        "language": rec.language,
                        "jurisdiction": rec.jurisdiction.value,
                        "is_authoritative": rec.is_authoritative,
                        "document_id": rec.provision.document_id,
                        "effective_from": rec.provision.effective_from.isoformat() if rec.provision.effective_from else None,
                        "effective_until": rec.provision.effective_until.isoformat() if rec.provision.effective_until else None,
                        "effective_from_ordinal": rec.provision.effective_from.toordinal() if rec.provision.effective_from else _OPEN_ENDED_FROM,
                        "effective_until_ordinal": rec.provision.effective_until.toordinal() if rec.provision.effective_until else _OPEN_ENDED_UNTIL,
                        "legal_domain": rec.provision.legal_domain,
                        "provision_json": rec.provision.model_dump_json(),
                    },
                )
            )
        client.upsert(collection_name=self.collection_name, points=points)

    def query(
        self,
        vector: Sequence[float],
        filters: RetrievalFilters | None = None,
        top_k: int = 5,
    ) -> list[RankedCandidate]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if len(vector) != self.dimension:
            raise ValueError(f"Query vector dimension mismatch: expected {self.dimension}, got {len(vector)}")
        client = self._load()
        from qdrant_client.http import models

        must_conditions: list[Any] = []
        if filters:
            if filters.jurisdiction != Jurisdiction.UNKNOWN:
                must_conditions.append(
                    models.FieldCondition(
                        key="jurisdiction",
                        match=models.MatchValue(value=filters.jurisdiction.value),
                    )
                )
            if filters.authoritative_only:
                must_conditions.append(
                    models.FieldCondition(
                        key="is_authoritative",
                        match=models.MatchValue(value=True),
                    )
                )
            if not filters.include_translations and filters.language in (Language.EN, Language.AR):
                must_conditions.append(
                    models.FieldCondition(
                        key="language",
                        match=models.MatchValue(value=filters.language.value),
                    )
                )
            if filters.document_ids:
                must_conditions.append(
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchAny(any=list(filters.document_ids)),
                    )
                )
            if filters.legal_domain:
                must_conditions.append(
                    models.FieldCondition(
                        key="legal_domain",
                        match=models.MatchValue(value=filters.legal_domain),
                    )
                )
            if filters.as_of is not None:
                ordinal = filters.as_of.toordinal()
                must_conditions.extend(
                    [
                        models.FieldCondition(
                            key="effective_from_ordinal",
                            range=models.Range(lte=ordinal),
                        ),
                        models.FieldCondition(
                            key="effective_until_ordinal",
                            range=models.Range(gte=ordinal),
                        ),
                    ]
                )

        qdrant_filter = models.Filter(must=must_conditions) if must_conditions else None

        limit = max(top_k * 3, top_k)
        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                query_filter=qdrant_filter,
                limit=limit,
            )
            results = response.points
        else:
            results = client.search(
                collection_name=self.collection_name,
                query_vector=list(vector),
                query_filter=qdrant_filter,
                limit=limit,
            )

        candidates: list[RankedCandidate] = []
        for rank, res in enumerate(results, start=1):
            prov = LegalProvision.model_validate_json(res.payload["provision_json"])
            if not matches_retrieval_filters(prov, filters):
                continue
            candidates.append(
                RankedCandidate(
                    alignment_id=res.payload["alignment_id"] or res.payload["provision_id"],
                    provision_id=res.payload["provision_id"],
                    dense_rank=rank,
                    dense_score=res.score,
                    fused_score=res.score,
                    provision=prov,
                )
            )
        return candidates[:top_k]

    def delete(self, provision_ids: Sequence[str]) -> None:
        if not provision_ids:
            return
        client = self._load()
        from qdrant_client.http import models

        point_ids = [str(uuid5(NAMESPACE_URL, f"uae-lexagent:{pid}")) for pid in provision_ids]
        client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=point_ids),
        )
