"""Data contracts and protocols for the retrieval engine."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..models import Jurisdiction, Language, LegalEvidence, LegalProvision, LegalQuery


class RetrievalFilters(BaseModel):
    """Hard metadata filtering criteria applied before dense and sparse search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    as_of: date | None = None
    language: Language = Language.UNKNOWN
    include_translations: bool = True
    authoritative_only: bool = False
    document_ids: tuple[str, ...] = ()
    legal_domain: str | None = None


def matches_retrieval_filters(provision: LegalProvision, filters: RetrievalFilters | None) -> bool:
    """Apply the canonical hard metadata policy to one provision."""
    if filters is None:
        return True
    if (
        filters.jurisdiction != Jurisdiction.UNKNOWN
        and provision.jurisdiction != filters.jurisdiction
    ):
        return False
    if filters.as_of is not None:
        if provision.effective_from is not None and filters.as_of < provision.effective_from:
            return False
        if provision.effective_until is not None and filters.as_of > provision.effective_until:
            return False
    if filters.authoritative_only and not provision.is_authoritative:
        return False
    if (
        not filters.include_translations
        and filters.language == Language.EN
        and provision.language != "en"
    ):
        return False
    if (
        not filters.include_translations
        and filters.language == Language.AR
        and provision.language != "ar"
    ):
        return False
    if filters.document_ids and provision.document_id not in filters.document_ids:
        return False
    if filters.legal_domain and provision.legal_domain != filters.legal_domain:
        return False
    return True


class IndexedProvision(BaseModel):
    """Internal indexed representation of a legal provision."""

    model_config = ConfigDict(extra="forbid")

    provision: LegalProvision
    tokens: list[str] = Field(default_factory=list)
    vector: list[float] | None = None

    @property
    def provision_id(self) -> str:
        return self.provision.provision_id

    @property
    def alignment_id(self) -> str | None:
        return self.provision.alignment_id

    @property
    def language(self) -> str:
        return self.provision.language

    @property
    def jurisdiction(self) -> Jurisdiction:
        return self.provision.jurisdiction

    @property
    def is_authoritative(self) -> bool:
        return self.provision.is_authoritative


class RankedCandidate(BaseModel):
    """A scored and ranked candidate provision during retrieval fusion."""

    model_config = ConfigDict(extra="forbid")

    alignment_id: str = Field(min_length=1)
    provision_id: str = Field(min_length=1)
    dense_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = None
    sparse_rank: int | None = Field(default=None, ge=1)
    sparse_score: float | None = None
    fused_score: float = 0.0
    provision: LegalProvision | None = None


class RetrieverProtocol(Protocol):
    """Shared retrieval protocol implemented by all retrieval backends."""

    def search(
        self,
        query: LegalQuery,
        *,
        top_k: int = 5,
        filters: RetrievalFilters | None = None,
    ) -> list[LegalEvidence]: ...
