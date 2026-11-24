"""Canonical contracts shared by ingestion, retrieval, agents, and evaluation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


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


class AuthenticityTier(str, Enum):
    GOV_AUTOMATED_TLS_VERIFIED = "GOV_AUTOMATED_TLS_VERIFIED"
    GOV_INDEPENDENTLY_CORROBORATED = "GOV_INDEPENDENTLY_CORROBORATED"
    OPERATOR_ATTESTED_ONLY = "OPERATOR_ATTESTED_ONLY"
    LEGACY_UNATTESTED = "LEGACY_UNATTESTED"


class CorroborationType(str, Enum):
    NONE_OPERATOR_ONLY = "none_operator_only"
    AUTOMATED_MIRROR_MATCH = "automated_mirror_match"
    GOV_PDF_DIGITAL_SIGNATURE = "gov_pdf_digital_signature"
    AUTOMATED_HTML_CROSS_CHECK = "automated_html_cross_check"


class RedistributionClearance(str, Enum):
    VERIFIED_PUBLIC_LAW = "verified_public_law"
    RESTRICTED_DO_NOT_COMMIT = "restricted_do_not_commit"
    PENDING_REVIEW = "pending_review"


class DocumentCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL_VERIFIED = "partial_verified"
    PARTIAL_MVP = "partial_mvp"
    UNINGESTED = "uningested"


class PublicationState(str, Enum):
    VERIFIED_PUBLISHABLE = "VERIFIED_PUBLISHABLE"
    PUBLISHED_WITH_CAVEAT = "PUBLISHED_WITH_CAVEAT"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
    UNRESOLVED_HISTORICAL = "UNRESOLVED_HISTORICAL"
    BLOCKED_LEGAL_TOPIC = "BLOCKED_LEGAL_TOPIC"


class LifecycleState(str, Enum):
    IN_FORCE = "in_force"
    SUPERSEDED = "superseded"
    REPEALED = "repealed"
    ENACTED_PENDING_COMMENCEMENT = "enacted_pending_commencement"
    EFFECTIVE_DATE_UNRESOLVED = "effective_date_unresolved"
    SUSPENDED_GAP = "suspended_gap"


def _absolute_uri(value: str) -> str:
    parsed = urlsplit(value)
    if (
        not parsed.scheme
        or any(ord(char) <= 32 or ord(char) >= 127 or char == "\\" for char in value)
        or (parsed.scheme in {"http", "https"} and not parsed.hostname)
    ):
        raise ValueError("Expected an absolute URI")
    if parsed.netloc:
        _ = parsed.port
    return value


def _date_time_input(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str) or value[10:11] not in {"T", "t"}:
        raise ValueError("Expected a timezone-aware date-time")
    return value


class ManualAttestation(BaseModel):
    """Operator attestation record required for manually acquired sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attestation_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    captured_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    operator_id: str = Field(min_length=1)
    acquired_at_utc: AwareDatetime
    claimed_source_url: str = Field(min_length=1)
    portal_navigation_steps: str = Field(min_length=10)
    capture_evidence_ref: str = Field(min_length=1)
    independent_cross_check_ref: str = Field(min_length=1)
    independent_cross_check_passed: StrictBool
    corroboration_type: CorroborationType
    notes: str | None = None

    _validate_uri = field_validator("claimed_source_url")(_absolute_uri)
    _validate_datetime = field_validator("acquired_at_utc", mode="before")(_date_time_input)

    @model_validator(mode="after")
    def validate_corroboration(self) -> "ManualAttestation":
        if (
            self.independent_cross_check_passed
            and self.corroboration_type == CorroborationType.NONE_OPERATOR_ONLY
        ):
            raise ValueError(
                "corroboration_type cannot be none_operator_only when independent_cross_check_passed is True"
            )
        return self


class HttpAcquisitionEvidence(BaseModel):
    """HTTP metadata captured with the transport evidence on a completed acquisition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    final_url: str
    redirect_history: tuple[str, ...] = Field(max_length=5)
    status_code: Literal[200]
    content_type: Literal["application/pdf", "text/html"]
    response_headers: dict[str, str]

    _validate_uri = field_validator("final_url")(_absolute_uri)

    @field_validator("redirect_history")
    @classmethod
    def validate_redirect_uris(cls, urls: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_absolute_uri(url) for url in urls)


class BoundaryRegistry(BaseModel):
    """Certified topic coverage registry for closed-world retrieval gating."""

    model_config = ConfigDict(extra="forbid")

    verified_topics: tuple[str, ...] = Field(default_factory=tuple)
    post_cutoff_amendments: dict[str, str] = Field(default_factory=dict)
    coverage_status: Literal["COMPLETE", "INCOMPLETE", "UNKNOWN"] = "UNKNOWN"


class _ValidatedRecord(BaseModel):
    """Preserve legacy assignment without leaving a record invalid after rejection."""

    model_config = ConfigDict(extra="forbid")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self).model_fields:
            fields = {field: getattr(self, field) for field in type(self).model_fields}
            fields[name] = value
            value = getattr(type(self).model_validate(fields), name)
        super().__setattr__(name, value)


class LegalSource(_ValidatedRecord):
    """Manifest record tracking provenance and integrity of a raw official document."""

    source_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    language: Literal["en", "ar"]
    publisher: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    retrieved_at: AwareDatetime
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    page_count: int | None = Field(default=None, ge=1)
    is_authoritative: StrictBool
    artifact_type: Literal["raw_html", "raw_pdf", "derived_text"] = "raw_pdf"
    source_role: Literal["extraction_primary", "archival_reference"] = "extraction_primary"
    derived_from_source_id: str | None = None
    extraction_tool: str | None = None
    extraction_version: str | None = None
    extraction_method: Literal["manual", "html_dom", "pdf_text", "ocr"]
    authenticity_tier: AuthenticityTier = AuthenticityTier.LEGACY_UNATTESTED
    attestation_id: str | None = None
    tls_cert_fingerprint: str | None = Field(default=None, pattern=r"^SHA256:[a-fA-F0-9]{64}$")
    redistribution_clearance: RedistributionClearance = RedistributionClearance.PENDING_REVIEW
    http_capture: HttpAcquisitionEvidence | None = None

    _validate_uri = field_validator("source_url")(_absolute_uri)
    _validate_datetime = field_validator("retrieved_at", mode="before")(_date_time_input)

    @model_validator(mode="after")
    def validate_artifact_lineage(self) -> "LegalSource":
        if self.artifact_type == "derived_text":
            if not self.derived_from_source_id:
                raise ValueError("derived_text sources must specify derived_from_source_id")
            if not self.extraction_tool or not self.extraction_version:
                raise ValueError("derived_text sources must specify extraction tool and version")
        elif self.derived_from_source_id is not None:
            raise ValueError(f"{self.artifact_type} sources cannot specify derived_from_source_id")
        return self

    @model_validator(mode="after")
    def validate_authenticity_and_authority(self) -> "LegalSource":
        if self.authenticity_tier in (
            AuthenticityTier.OPERATOR_ATTESTED_ONLY,
            AuthenticityTier.GOV_INDEPENDENTLY_CORROBORATED,
        ):
            if not self.attestation_id:
                raise ValueError(f"{self.authenticity_tier.value} sources require attestation_id")
        if (
            self.authenticity_tier != AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED
            and self.tls_cert_fingerprint is not None
        ):
            raise ValueError("Only automated TLS acquisition can record a TLS fingerprint")
        if self.authenticity_tier == AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED:
            if not self.tls_cert_fingerprint:
                raise ValueError("GOV_AUTOMATED_TLS_VERIFIED sources require tls_cert_fingerprint")
            if self.attestation_id is not None:
                raise ValueError("Automated acquisition cannot claim operator attestation")
        if self.http_capture is not None:
            if self.authenticity_tier != AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED:
                raise ValueError("HTTP capture requires automated TLS verification")
            capture = self.http_capture
            first_url = (
                capture.redirect_history[0] if capture.redirect_history else capture.final_url
            )
            expected_type = {"raw_pdf": "application/pdf", "raw_html": "text/html"}
            if (
                first_url != self.source_url
                or expected_type.get(self.artifact_type) != capture.content_type
            ):
                raise ValueError("HTTP evidence contradicts the source URL or artifact format")
        if self.is_authoritative and self.language != "ar":
            raise ValueError("Authoritative sources must be Arabic ('ar')")
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
    gazette_number: str | None = None
    completeness: DocumentCompleteness = DocumentCompleteness.PARTIAL_MVP
    missing_article_numbers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_effective_window(self) -> "LegalDocument":
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective_until must not precede effective_from")
        return self


class LegalProvision(_ValidatedRecord):
    """An article/clause-sized unit suitable for citation and retrieval."""

    provision_id: str = Field(min_length=1)
    logical_provision_id: str | None = None
    version_id: str | None = None
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    jurisdiction: Jurisdiction
    legal_domain: str = Field(min_length=1)
    article: str = Field(min_length=1)
    clause: str | None = None
    subclause: str | None = None
    language: Literal["en", "ar"]
    is_authoritative: StrictBool
    alignment_id: str | None = None
    translation_of: str | None = None
    translation_provenance: (
        Literal[
            "authoritative_original",
            "official_government_translation",
            "unavailable_official",
        ]
        | None
    ) = None
    text: str = Field(min_length=1)
    normalized_text: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    uncertainty_window_start: date | None = None
    uncertainty_window_end: date | None = None
    lifecycle_state: LifecycleState = LifecycleState.IN_FORCE
    gap_or_repeal_reason: str | None = None
    publication_state: PublicationState = PublicationState.LEGACY_UNVERIFIED
    caveat_codes: tuple[str, ...] = ()
    source_url: str = Field(min_length=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    extraction_fidelity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    cmap_corruption_detected: StrictBool = False
    human_verification_status: Literal["unreviewed", "extraction_verified", "rejected"] = (
        "unreviewed"
    )
    reviewer_id: str | None = None
    reviewed_at_utc: AwareDatetime | None = None

    _validate_uri = field_validator("source_url")(_absolute_uri)
    _validate_datetime = field_validator("reviewed_at_utc", mode="before")(_date_time_input)

    @model_validator(mode="after")
    def default_ids_and_authoritative(self) -> "LegalProvision":
        if self.logical_provision_id is None:
            object.__setattr__(self, "logical_provision_id", self.alignment_id or self.provision_id)
        if self.version_id is None:
            object.__setattr__(
                self, "version_id", f"{self.alignment_id or self.provision_id}@legacy"
            )

        if self.is_authoritative:
            if self.language != "ar":
                raise ValueError("Only Arabic provisions can be marked is_authoritative=True")
            if self.translation_provenance is None:
                object.__setattr__(self, "translation_provenance", "authoritative_original")
            elif self.translation_provenance != "authoritative_original":
                raise ValueError(
                    "Authoritative Arabic provisions must have translation_provenance='authoritative_original'"
                )
        return self

    @model_validator(mode="after")
    def valid_authority_translation_and_pages(self) -> "LegalProvision":
        if self.language == "ar" and not self.is_authoritative:
            raise ValueError("Arabic legal provisions must be marked is_authoritative=True")
        if self.language == "en" and self.is_authoritative:
            raise ValueError("English legal translations cannot be marked is_authoritative=True")
        if self.language == "en" and not self.translation_of:
            raise ValueError(
                "English legal translations must specify translation_of pointing to Arabic provision ID"
            )
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
        if (
            self.uncertainty_window_start is not None
            and self.uncertainty_window_end is not None
            and self.uncertainty_window_end < self.uncertainty_window_start
        ):
            raise ValueError("uncertainty_window_end cannot be less than uncertainty_window_start")
        return self

    @model_validator(mode="after")
    def validate_publication_and_lifecycle(self) -> "LegalProvision":
        if self.publication_state == PublicationState.VERIFIED_PUBLISHABLE:
            if self.human_verification_status != "extraction_verified":
                raise ValueError(
                    "VERIFIED_PUBLISHABLE provisions must have human_verification_status='extraction_verified'"
                )
            if not self.reviewer_id or not self.reviewed_at_utc:
                raise ValueError(
                    "VERIFIED_PUBLISHABLE provisions must specify reviewer_id and reviewed_at_utc"
                )
            if self.cmap_corruption_detected:
                raise ValueError(
                    "VERIFIED_PUBLISHABLE provisions cannot have cmap_corruption_detected=True"
                )
            if self.caveat_codes:
                raise ValueError("VERIFIED_PUBLISHABLE provisions cannot carry active caveat_codes")

        elif self.publication_state == PublicationState.PUBLISHED_WITH_CAVEAT:
            if not self.caveat_codes:
                raise ValueError(
                    "PUBLISHED_WITH_CAVEAT provisions must carry at least one caveat code"
                )

        elif self.publication_state == PublicationState.UNRESOLVED_HISTORICAL:
            if not self.uncertainty_window_start or not self.uncertainty_window_end:
                raise ValueError(
                    "UNRESOLVED_HISTORICAL provisions require explicit uncertainty_window_start and uncertainty_window_end"
                )

        if (
            self.lifecycle_state == LifecycleState.IN_FORCE
            and self.effective_from is None
            and self.publication_state != PublicationState.LEGACY_UNVERIFIED
        ):
            raise ValueError("In-force, non-legacy provisions require effective_from")
        if self.lifecycle_state == LifecycleState.SUPERSEDED:
            if self.effective_from is None or self.effective_until is None:
                raise ValueError(
                    "SUPERSEDED provisions must have both effective_from and effective_until"
                )
        elif self.lifecycle_state in (LifecycleState.REPEALED, LifecycleState.SUSPENDED_GAP):
            if not self.gap_or_repeal_reason:
                raise ValueError(
                    f"{self.lifecycle_state.value} provisions require gap_or_repeal_reason"
                )

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
    aligned_provisions: list[LegalProvision] = Field(default_factory=list)


class Claim(BaseModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    claim_key: str = ""
    cited_alignment_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    valid_schema: bool = False
    statutory_basis: str | None = None
    execution_latency_ms: float = Field(default=0.0, ge=0.0)


class AgentTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_index: int = Field(ge=0)
    node_name: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: str = Field(min_length=1)
    rationale: str
    duration_ms: float = Field(ge=0.0)
    input_summary: str = ""
    output_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    trace: list[AgentTraceStep] = Field(default_factory=list)
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


class AdversarialType(str, Enum):
    OUT_OF_JURISDICTION = "out_of_jurisdiction"
    NON_EXISTENT_ARTICLE = "non_existent_article"
    COUNTERFACTUAL_PREMISE = "counterfactual_premise"
    JURISDICTION_DISTRACTOR = "jurisdiction_distractor"
    TEMPORAL_VERSIONING = "temporal_versioning"
    CITATION_VERIFICATION = "citation_verification"


class ExpectedClaim(BaseModel):
    """A distinct legally-verifiable claim expected in the answer."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_key: str = Field(default="")
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
            raise ValueError(
                f"Case '{self.case_id}' is marked expected_answerable=False but lacks abstention_reason"
            )
        if not self.expected_answerable and self.expected_provisions:
            raise ValueError(
                f"Unanswerable case '{self.case_id}' cannot declare expected provisions"
            )
        if self.category == BenchmarkCategory.TEMPORAL_VERSIONING and self.as_of is None:
            raise ValueError(f"Temporal case '{self.case_id}' must specify as_of date")
        if self.category == BenchmarkCategory.ADVERSARIAL and not self.adversarial_type:
            raise ValueError(f"Adversarial case '{self.case_id}' must specify adversarial_type")
        if self.category == BenchmarkCategory.CODE_SWITCHED and self.language is not Language.MIXED:
            raise ValueError(f"Code-switched case '{self.case_id}' must use language='mixed'")
        if self.category == BenchmarkCategory.TOOL_CALCULATION:
            if self.expected_tool is None or self.expected_tool.calculation is None:
                raise ValueError(
                    f"Tool-calculation case '{self.case_id}' requires expected calculation data"
                )
        return self


class PredictedClaim(BaseModel):
    """A claim emitted by the evaluated system with its supporting references."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_key: str = Field(default="")
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
    metadata: dict[str, Any] = Field(default_factory=dict)


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
