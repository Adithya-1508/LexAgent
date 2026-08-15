"""Bilingual hybrid legal retrieval package for UAE-LexAgent."""

from .baseline import InMemoryRetriever
from .contracts import (
    IndexedProvision,
    RankedCandidate,
    RetrievalFilters,
    RetrieverProtocol,
    matches_retrieval_filters,
)
from .embeddings import (
    DeterministicHashEmbedding,
    EmbeddingProvider,
    OpenAIEmbedding,
    SentenceTransformerEmbedding,
    get_embedding_provider,
)

from .fusion import reciprocal_rank_fusion
from .hybrid import HybridLegalRetriever
from .indexing import RetrievalIndex, build_retrieval_index
from .reranker import (
    IdentityReranker,
    Reranker,
    SentenceTransformerReranker,
)
from .sparse import BM25Index, normalize_arabic, tokenize_legal_text
from .vector_store import InMemoryVectorStore, QdrantVectorStore, VectorStore

__all__ = [
    "BM25Index",
    "DeterministicHashEmbedding",
    "EmbeddingProvider",
    "HybridLegalRetriever",
    "IdentityReranker",
    "InMemoryRetriever",
    "InMemoryVectorStore",
    "IndexedProvision",
    "OpenAIEmbedding",
    "QdrantVectorStore",
    "RankedCandidate",
    "Reranker",
    "RetrievalFilters",
    "RetrievalIndex",
    "RetrieverProtocol",
    "SentenceTransformerEmbedding",
    "SentenceTransformerReranker",
    "VectorStore",
    "build_retrieval_index",
    "get_embedding_provider",
    "matches_retrieval_filters",
    "normalize_arabic",
    "reciprocal_rank_fusion",
    "tokenize_legal_text",
]
