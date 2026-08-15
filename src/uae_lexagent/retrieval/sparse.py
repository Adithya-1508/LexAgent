"""Pure-Python BM25 sparse retrieval engine with legal tokenization."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import Sequence

from ..models import Jurisdiction, Language, LegalProvision
from .contracts import IndexedProvision, RankedCandidate, RetrievalFilters, matches_retrieval_filters

_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670]")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*|[\u0600-\u06ff]+")


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for statutory token matching."""
    text = _ARABIC_DIACRITICS.sub("", text)
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    return text


def light_stem_arabic(token: str) -> str:
    """Apply conservative prefix stripping for Arabic legal tokens."""
    if len(token) > 4 and (token.startswith("و") or token.startswith("ف")):
        token = token[1:]
    if len(token) > 4 and token.startswith("ال"):
        token = token[2:]
    return token


def tokenize_legal_text(text: str, language: Language | str = Language.UNKNOWN) -> list[str]:
    """Tokenize legal query or provision text into normalized keywords."""
    raw_tokens = _TOKEN_PATTERN.findall(text.casefold())
    processed_tokens: list[str] = []

    for t in raw_tokens:
        if re.search(r"[\u0600-\u06ff]", t):
            norm_t = normalize_arabic(t)
            stem_t = light_stem_arabic(norm_t)
            if stem_t:
                processed_tokens.append(stem_t)
        else:
            processed_tokens.append(t)

    return processed_tokens


def _is_temporally_valid(provision: LegalProvision, as_of: date | None) -> bool:
    if as_of is None:
        return True
    if provision.effective_from is not None and as_of < provision.effective_from:
        return False
    if provision.effective_until is not None and as_of > provision.effective_until:
        return False
    return True


class BM25Index:
    """Pure-Python Okapi BM25 index with pre-filtering and deterministic ranking."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0.0:
            raise ValueError("k1 must be non-negative")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be between 0.0 and 1.0")
        self.k1 = k1
        self.b = b
        self._records: list[IndexedProvision] = []
        self._doc_lens: list[int] = []
        self._avg_dl: float = 0.0
        self._doc_freqs: dict[str, int] = Counter()
        self._term_freqs: list[dict[str, int]] = []
        self._seen_ids: set[str] = set()

    def add_provision(self, provision: LegalProvision) -> None:
        """Index a single legal provision."""
        if provision.provision_id in self._seen_ids:
            raise ValueError(f"Duplicate provision_id: '{provision.provision_id}'")
        self._seen_ids.add(provision.provision_id)

        tokens = tokenize_legal_text(provision.text, provision.language)
        record = IndexedProvision(provision=provision, tokens=tokens)
        self._records.append(record)

        tf = Counter(tokens)
        self._term_freqs.append(dict(tf))
        self._doc_lens.append(len(tokens))

        for term in tf.keys():
            self._doc_freqs[term] += 1

        self._avg_dl = sum(self._doc_lens) / len(self._doc_lens) if self._doc_lens else 0.0

    def build(self, provisions: Sequence[LegalProvision]) -> None:
        """Batch index provisions."""
        self._records.clear()
        self._doc_lens.clear()
        self._doc_freqs.clear()
        self._term_freqs.clear()
        self._seen_ids.clear()
        for p in provisions:
            self.add_provision(p)

    def _idf(self, term: str) -> float:
        n = self._doc_freqs.get(term, 0)
        N = len(self._records)
        return math.log(((N - n + 0.5) / (n + 0.5)) + 1.0)

    def query(
        self,
        query_tokens: list[str],
        filters: RetrievalFilters | None = None,
        top_k: int = 5,
    ) -> list[RankedCandidate]:
        """Score candidate provisions using BM25 with hard metadata pre-filtering."""
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self._records or not query_tokens:
            return []

        scored: list[tuple[float, IndexedProvision]] = []

        for idx, record in enumerate(self._records):
            prov = record.provision

            if not matches_retrieval_filters(prov, filters):
                continue

            # BM25 Scoring
            score = 0.0
            doc_tf = self._term_freqs[idx]
            doc_len = self._doc_lens[idx]
            denom_const = self.k1 * (1.0 - self.b + self.b * (doc_len / (self._avg_dl or 1.0)))

            for q_term in set(query_tokens):
                if q_term in doc_tf:
                    freq = doc_tf[q_term]
                    idf = self._idf(q_term)
                    term_score = idf * ((freq * (self.k1 + 1.0)) / (freq + denom_const))
                    score += term_score

            if score > 0.0:
                scored.append((score, record))

        # Deterministic sorting by score descending, then alignment_id, then provision_id
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
                    sparse_rank=rank,
                    sparse_score=score,
                    fused_score=score,
                    provision=rec.provision,
                )
            )

        return candidates
