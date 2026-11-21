"""Embedding providers for dense vector retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol, Sequence


class EmbeddingProvider(Protocol):
    """Protocol for dense vector embedding models."""

    @property
    def dimension(self) -> int: ...

    @property
    def model_id(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class DeterministicHashEmbedding:
    """Zero-dependency, deterministic unit-normalized hash embedder with sub-word n-grams."""

    def __init__(self, dimension: int = 128, model_id: str | None = None) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension
        self._model_id = model_id or f"deterministic-hash-{dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    def _embed_single(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        tokens = re.findall(r"[\w\u0600-\u06ff]+", text.casefold())

        if not tokens:
            vec[0] = 1.0
            return vec

        for token in tokens:
            # Word token feature
            h_token = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
            idx_token = h_token % self._dimension
            vec[idx_token] += 2.0

            # Sub-word character trigrams for morphological matching
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    trigram = token[i : i + 3]
                    h_tri = int(
                        hashlib.md5(trigram.encode("utf-8"), usedforsecurity=False).hexdigest()[:8],
                        16,
                    )
                    idx_tri = h_tri % self._dimension
                    vec[idx_tri] += 1.0

        # L2 Unit Normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        else:
            vec[0] = 1.0
        return vec

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_single(t) for t in texts]


class SentenceTransformerEmbedding:
    """Multilingual dense embedder using sentence-transformers (lazy-loaded)."""

    def __init__(self, model_id: str = "BAAI/bge-m3") -> None:
        self._model_id = model_id
        self._model: Any = None
        self._dimension: int | None = None

    def _load(self) -> None:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_id)
                self._dimension = self._model.get_sentence_embedding_dimension()
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is not installed. Install with `pip install sentence-transformers`"
                ) from exc

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._load()
        return self._dimension or 1024

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._load()
        embeddings = self._model.encode(list(texts), normalize_embeddings=True)
        return embeddings.tolist()


class OpenAIEmbedding:
    """Cloud dense embedder using OpenAI embeddings API (lazy-loaded)."""

    _MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self, model_id: str = "text-embedding-3-small", dimension: int | None = None
    ) -> None:
        resolved_dimension = dimension or self._MODEL_DIMENSIONS.get(model_id)
        if resolved_dimension is None or resolved_dimension <= 0:
            raise ValueError(
                f"Provide a positive dimension for unsupported OpenAI model '{model_id}'"
            )
        self._model_id = model_id
        self._dimension = resolved_dimension
        self._client: Any = None

    def _load(self) -> None:
        if self._client is None:
            try:
                import openai

                self._client = openai.OpenAI()
            except ImportError as exc:
                raise ImportError(
                    "openai is not installed. Install with `pip install openai`"
                ) from exc

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._load()
        resp = self._client.embeddings.create(input=list(texts), model=self._model_id)
        return [item.embedding for item in resp.data]


def get_embedding_provider(
    provider_type: str = "hash",
    model_id: str | None = None,
) -> EmbeddingProvider:
    """Factory helper to obtain the configured embedding provider."""
    provider_type_clean = provider_type.casefold().strip()
    if provider_type_clean in ("hash", "deterministic", "mock"):
        return DeterministicHashEmbedding(model_id=model_id or "deterministic-hash-128")
    elif provider_type_clean in ("sentence_transformer", "sentence-transformer", "bge"):
        return SentenceTransformerEmbedding(model_id=model_id or "BAAI/bge-m3")
    elif provider_type_clean in ("openai", "text-embedding"):
        return OpenAIEmbedding(model_id=model_id or "text-embedding-3-small")
    else:
        raise ValueError(f"Unknown embedding provider: '{provider_type}'")
