"""Canonical contracts shared by ingestion, retrieval, agents, and evaluation."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Language(str, Enum):
    EN = "en"
    AR = "ar"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Jurisdiction(str, Enum):
    UAE_ONSHORE = "uae_onshore"
    DIFC = "difc"
    ADGM = "adgm"
    OTHER_FREE_ZONE = "other_free_zone"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    OFFICIAL = "official"
    SECONDARY = "secondary"
    USER_PROVIDED = "user_provided"


class LegalSource(BaseModel):
    """Manifest record tracking provenance and integrity of a raw official document."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    language: Literal["en", "ar"]
    publisher: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    retrieved_at: datetime
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    page_count: int | None = Field(default=None, ge=1)
    is_authoritative: bool
    artifact_type: Literal["raw_pdf", "derived_text"] = "raw_pdf"
    derived_from_source_id: str | None = None
    extraction_tool: str | None = None
    extraction_version: str | None = None
    extraction_method: Literal["manual", "pdf_text", "ocr"]

    @model_validator(mode="after")
    def validate_artifact_lineage(self) -> "LegalSource":
        if self.artifact_type == "derived_text":
            if not self.derived_from_source_id:
                raise ValueError("derived_text sources must specify derived_from_source_id")
            if not self.extraction_tool or not self.extraction_version:
                raise ValueError("derived_text sources must specify extraction tool and version")
        elif self.derived_from_source_id:
            raise ValueError("raw_pdf sources cannot specify derived_from_source_id")
        return self


class LegalDocument(BaseModel):
    """A versioned legal source; this is the corpus source-of-truth record."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    title_ar: str | None = None
    jurisdiction: Jurisdiction
    legal_domain: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_type: SourceType
    published_date: date | None = None
    issued_date: date | None = None
    official_gazette_date: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    last_updated: date | None = None
    version: str = Field(default="original", min_length=1)
    is_consolidated: bool = False
    status: Literal["active", "superseded", "partial_mvp"] = "active"
    authoritative_language: Literal["ar"] = "ar"
    source_ids: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    coverage_status: Literal["complete", "partial_mvp"] = "partial_mvp"

    @model_validator(mode="after")
    def valid_effective_window(self) -> "LegalDocument":
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until must not precede effective_from")
        return self


class LegalProvision(BaseModel):
    """An article/clause-sized unit suitable for citation and retrieval."""

    model_config = ConfigDict(extra="forbid")

    provision_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    jurisdiction: Jurisdiction
    legal_domain: str = Field(min_length=1)
    article: str = Field(min_length=1)
    clause: str | None = None
    subclause: str | None = None
    language: Literal["en", "ar"]
    is_authoritative: bool
    alignment_id: str | None = None
    translation_of: str | None = None
    text: str = Field(min_length=1)
    effective_from: date | None = None
    effective_until: date | None = None
    source_url: str = Field(min_length=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_authority_translation_and_pages(self) -> "LegalProvision":
        if self.language == "ar" and not self.is_authoritative:
            raise ValueError("Arabic legal provisions must be marked is_authoritative=True")
        if self.language == "en" and self.is_authoritative:
            raise ValueError("English legal translations cannot be marked is_authoritative=True")
        if self.language == "en" and not self.translation_of:
            raise ValueError("English legal translations must specify translation_of pointing to Arabic provision ID")
        if self.language == "ar" and self.translation_of is not None:
            raise ValueError("Arabic legal provisions cannot point to a translation_of provision")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end cannot be less than page_start")
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until cannot be less than effective_from")
        return self


class LegalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: Language = Language.UNKNOWN
    intent: str | None = None
    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    as_of: date | None = None
    risk_level: Literal["low", "medium", "high", "unknown"] = "unknown"


class LegalEvidence(BaseModel):
    """A retrieved provision plus scores needed for audit and benchmarking."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    provision: LegalProvision
    dense_score: float | None = None
    sparse_score: float | None = None
    reranker_score: float | None = None
    retrieval_rank: int = Field(ge=1)


class Claim(BaseModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool | None = None


class ToolCall(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    valid_schema: bool = False


class VerificationResult(BaseModel):
    passed: bool
    citation_valid: bool = False
    claims_grounded: bool = False
    jurisdiction_valid: bool = False
    temporal_valid: bool = False
    bilingual_consistent: bool | None = None
    schema_valid: bool = False
    issues: list[str] = Field(default_factory=list)


class LegalResponse(BaseModel):
    query_id: str = Field(min_length=1)
    answerable: bool
    answer: str
    language: Literal["en", "ar"]
    jurisdiction: Jurisdiction
    evidence: list[LegalEvidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    verification: VerificationResult
    confidence: float = Field(ge=0.0, le=1.0)
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
