"""A transparent local retrieval baseline used before vector infrastructure."""

from __future__ import annotations

import re
from datetime import date

from .models import Jurisdiction, Language, LegalEvidence, LegalProvision, LegalQuery


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u0600-\u06ff]+", text.casefold()))


def _is_temporally_valid(provision: LegalProvision, as_of: date | None) -> bool:
    if as_of is None:
        return True
    if provision.effective_from is not None and as_of < provision.effective_from:
        return False
    if provision.effective_until is not None and as_of > provision.effective_until:
        return False
    return True


class InMemoryRetriever:
    """Lexical baseline with the same metadata filters expected by Qdrant."""

    def __init__(self, provisions: list[LegalProvision] | None = None) -> None:
        self._provisions = list(provisions or [])

    def add(self, provision: LegalProvision) -> None:
        self._provisions.append(provision)

    def search(
        self,
        query: LegalQuery,
        *,
        top_k: int = 5,
        jurisdiction: Jurisdiction | None = None,
    ) -> list[LegalEvidence]:
        """Return provision-sized evidence after hard metadata filtering."""

        target_jurisdiction = jurisdiction or query.jurisdiction
        query_tokens = _tokens(query.text)
        scored: list[tuple[float, LegalProvision]] = []

        for provision in self._provisions:
            if (
                target_jurisdiction is not Jurisdiction.UNKNOWN
                and provision.jurisdiction is not target_jurisdiction
            ):
                continue
            if not _is_temporally_valid(provision, query.as_of):
                continue
            if query.language is Language.EN and provision.language != "en":
                continue
            if query.language is Language.AR and provision.language != "ar":
                continue

            provision_tokens = _tokens(provision.text)
            overlap = query_tokens & provision_tokens
            if not overlap:
                continue
            score = len(overlap) / max(len(query_tokens), 1)
            scored.append((score, provision))

        scored.sort(key=lambda item: (-item[0], item[1].provision_id))
        return [
            LegalEvidence(
                evidence_id=f"ev-{provision.provision_id}",
                provision=provision,
                sparse_score=score,
                retrieval_rank=rank,
            )
            for rank, (score, provision) in enumerate(scored[:top_k], start=1)
        ]

    def retrieve(
        self,
        query: str | LegalQuery,
        *,
        top_k: int = 5,
        jurisdiction: Jurisdiction | None = None,
        as_of: date | None = None,
        language: Language = Language.UNKNOWN,
    ) -> list[LegalEvidence]:
        """Convenience method accepting raw string queries."""
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

        return self.search(
            legal_query,
            top_k=top_k,
            jurisdiction=jurisdiction,
        )
