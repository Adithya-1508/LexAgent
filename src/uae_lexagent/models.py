"""Canonical contracts shared by ingestion, retrieval, agents, and evaluation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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


# =============================================================================
# Evaluation & Benchmark Models
# =============================================================================


class BenchmarkCategory(str, Enum):
    DIRECT_FACTUAL = "direct_factual"
    MULTI_HOP = "multi_hop"
    CODE_SWITCHED = "code_switched"
    TOOL_CALCULATION = "tool_calculation"
    ADVERSARIAL = "adversarial"
    OUT_OF_SCOPE = "out_of_scope"
    JURISDICTION_DISTRACTOR = "jurisdiction_distractor"
    TEMPORAL_VERSIONING = "temporal_versioning"
    CITATION_VERIFICATION = "citation_verification"


class ExpectedClaim(BaseModel):
    """A distinct legally-verifiable claim expected in the answer."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    required_conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)


class ExpectedCalculation(BaseModel):
    """Deterministic mathematical expectation with decimal precision."""

    model_config = ConfigDict(extra="forbid")

    basic_salary: Decimal = Field(gt=0)
    service_years: Decimal = Field(ge=0)
    expected_amount: Decimal = Field(ge=0)
    currency: str = Field(default="AED", min_length=1)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class ExpectedToolCall(BaseModel):
    """Tool invocation expectation for computational questions."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    calculation: ExpectedCalculation | None = None


class BenchmarkCase(BaseModel):
    """Ground-truth benchmark test case."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0.0", min_length=1)
    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    language: Language
    category: BenchmarkCategory
    expected_jurisdiction: Jurisdiction
    expected_answerable: bool
    abstention_reason: str | None = None
    expected_provisions: list[str] = Field(default_factory=list)  # Canonical alignment_ids
    expected_claims: list[ExpectedClaim] = Field(default_factory=list)
    expected_tool: ExpectedToolCall | None = None
    bilingual_pair_id: str | None = None
    as_of: date | None = None
    expected_corpus_version: str = Field(min_length=1)
    adversarial_type: str | None = None
    source_notes: str | None = None

    @model_validator(mode="after")
    def validate_case_consistency(self) -> "BenchmarkCase":
        if not self.expected_answerable and not self.abstention_reason:
            raise ValueError(f"Case '{self.case_id}' is marked expected_answerable=False but lacks abstention_reason")
        if not self.expected_answerable and self.expected_provisions:
            raise ValueError(f"Unanswerable case '{self.case_id}' cannot declare expected provisions")
        if self.category == BenchmarkCategory.TEMPORAL_VERSIONING and self.as_of is None:
            raise ValueError(f"Temporal case '{self.case_id}' must specify as_of date")
        if self.category == BenchmarkCategory.ADVERSARIAL and not self.adversarial_type:
            raise ValueError(f"Adversarial case '{self.case_id}' must specify adversarial_type")
        if self.category == BenchmarkCategory.CODE_SWITCHED and self.language is not Language.MIXED:
            raise ValueError(f"Code-switched case '{self.case_id}' must use language='mixed'")
        if self.category == BenchmarkCategory.TOOL_CALCULATION:
            if self.expected_tool is None or self.expected_tool.calculation is None:
                raise ValueError(f"Tool-calculation case '{self.case_id}' requires expected calculation data")
        return self


class PredictedClaim(BaseModel):
    """A claim emitted by the evaluated system with its supporting references."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    cited_alignment_ids: list[str] = Field(default_factory=list)
    supported: bool | None = None


class EvaluationPrediction(BaseModel):
    """Standardized prediction output produced by an evaluated agent or retriever."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    answerable: bool
    jurisdiction: Jurisdiction
    retrieved_alignment_ids: list[str] = Field(default_factory=list)
    cited_alignment_ids: list[str] = Field(default_factory=list)
    claims: list[PredictedClaim] = Field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    calculated_amount: Decimal | None = None
    latency_ms: float = Field(ge=0.0)


class DimensionScore(BaseModel):
    """Score for a specific Shubhi Signal evaluation dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    score: float | None = None
    sample_count: int = Field(ge=0)
    measured: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class ShubhiSignalScore(BaseModel):
    """Composite Shubhi Signal benchmark scorecard."""

    model_config = ConfigDict(extra="forbid")

    benchmark_version: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    dimensions: list[DimensionScore] = Field(default_factory=list)
    aggregate_score: float | None = None
    weights: dict[str, float] = Field(default_factory=dict)
    category_scores: dict[str, float] = Field(default_factory=dict)
    language_scores: dict[str, float] = Field(default_factory=dict)


class BenchmarkManifest(BaseModel):
    """Integrity metadata for a benchmark dataset and its corpus snapshot."""

    model_config = ConfigDict(extra="forbid")

    benchmark_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    benchmark_version: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    corpus_fingerprint: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    total_cases: int = Field(ge=1)
    coverage_status: Literal["complete", "partial_mvp"]
    created_at: datetime
    category_distribution: dict[str, int] = Field(default_factory=dict)
    dataset_path: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
