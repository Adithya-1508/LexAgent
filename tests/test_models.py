from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from uae_lexagent.models import (
    AuthenticityTier,
    BoundaryRegistry,
    CorroborationType,
    DocumentCompleteness,
    Jurisdiction,
    LegalDocument,
    LegalEvidence,
    LegalProvision,
    LegalSource,
    LifecycleState,
    ManualAttestation,
    PublicationState,
    RedistributionClearance,
    SourceType,
)


def test_document_and_provision_preserve_citation_metadata() -> None:
    document = LegalDocument(
        document_id="example-law-2026",
        title_en="Example Employment Law",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        source_url="https://example.test/law",
        source_type=SourceType.OFFICIAL,
        effective_from=date(2026, 1, 1),
        version="2026-01",
    )
    provision = LegalProvision(
        provision_id="example-law-2026-a9-c1-en",
        document_id=document.document_id,
        source_id="MOHRE_EX_2026_EN",
        jurisdiction=document.jurisdiction,
        legal_domain=document.legal_domain,
        article="9",
        clause="1",
        language="en",
        is_authoritative=False,
        translation_of="example-law-2026-a9-c1-ar",
        text="Example provision text.",
        alignment_id="example-law-2026-a9-c1",
        effective_from=document.effective_from,
        source_url=document.source_url,
    )
    evidence = LegalEvidence(
        evidence_id="ev-1",
        provision=provision,
        retrieval_rank=1,
        dense_score=0.91,
    )

    assert evidence.provision.article == "9"
    assert evidence.provision.alignment_id == "example-law-2026-a9-c1"
    assert evidence.provision.jurisdiction is Jurisdiction.UAE_ONSHORE


def test_document_rejects_invalid_effective_window() -> None:
    with pytest.raises(ValidationError):
        LegalDocument(
            document_id="bad-window",
            title_en="Bad Window",
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            legal_domain="employment",
            source_url="https://example.test/bad",
            source_type=SourceType.OFFICIAL,
            effective_from=date(2026, 2, 1),
            effective_until=date(2026, 1, 1),
        )


def test_source_rejects_invalid_sha256_and_incomplete_lineage() -> None:
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="bad-hash",
            document_id="doc",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="not-a-sha256",
            is_authoritative=False,
            extraction_method="pdf_text",
        )

    with pytest.raises(ValidationError):
        LegalSource(
            source_id="missing-parent",
            document_id="doc",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.txt",
            local_path="law.txt",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="0" * 64,
            is_authoritative=False,
            artifact_type="derived_text",
            extraction_method="pdf_text",
        )


def test_provision_rejects_invalid_page_and_effective_windows() -> None:
    common = dict(
        provision_id="bad-provision",
        document_id="doc",
        source_id="source",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        article="9",
        language="ar",
        is_authoritative=True,
        text="Text",
        source_url="https://mohre.gov.ae/law.pdf",
    )

    with pytest.raises(ValidationError):
        LegalProvision(**common, page_start=5, page_end=4)

    with pytest.raises(ValidationError):
        LegalProvision(
            **common,
            effective_from=date(2026, 2, 1),
            effective_until=date(2026, 1, 1),
        )


def test_authenticity_and_lifecycle_enums() -> None:
    assert AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED.value == "GOV_AUTOMATED_TLS_VERIFIED"
    assert AuthenticityTier.GOV_INDEPENDENTLY_CORROBORATED.value == "GOV_INDEPENDENTLY_CORROBORATED"
    assert AuthenticityTier.OPERATOR_ATTESTED_ONLY.value == "OPERATOR_ATTESTED_ONLY"
    assert AuthenticityTier.LEGACY_UNATTESTED.value == "LEGACY_UNATTESTED"

    assert CorroborationType.NONE_OPERATOR_ONLY.value == "none_operator_only"
    assert CorroborationType.AUTOMATED_MIRROR_MATCH.value == "automated_mirror_match"

    assert RedistributionClearance.VERIFIED_PUBLIC_LAW.value == "verified_public_law"
    assert RedistributionClearance.RESTRICTED_DO_NOT_COMMIT.value == "restricted_do_not_commit"
    assert RedistributionClearance.PENDING_REVIEW.value == "pending_review"

    assert DocumentCompleteness.COMPLETE.value == "complete"
    assert DocumentCompleteness.PARTIAL_VERIFIED.value == "partial_verified"
    assert DocumentCompleteness.PARTIAL_MVP.value == "partial_mvp"
    assert DocumentCompleteness.UNINGESTED.value == "uningested"

    assert PublicationState.VERIFIED_PUBLISHABLE.value == "VERIFIED_PUBLISHABLE"
    assert PublicationState.PUBLISHED_WITH_CAVEAT.value == "PUBLISHED_WITH_CAVEAT"
    assert PublicationState.LEGACY_UNVERIFIED.value == "LEGACY_UNVERIFIED"

    assert LifecycleState.IN_FORCE.value == "in_force"
    assert LifecycleState.SUPERSEDED.value == "superseded"


def test_manual_attestation_validation() -> None:
    valid_attestation = ManualAttestation(
        attestation_id="attest-001",
        source_id="src-001",
        operator_id="operator-42",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://mohre.gov.ae/decree.pdf",
        portal_navigation_steps="Logged in, clicked legislations, searched 33/2021, clicked download.",
        capture_evidence_ref="evidence/screen_01.png",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )
    assert valid_attestation.attestation_id == "attest-001"

    # Reject none_operator_only when independent_cross_check_passed is True
    with pytest.raises(ValidationError):
        ManualAttestation(
            attestation_id="attest-bad",
            source_id="src-001",
            operator_id="operator-42",
            acquired_at_utc=datetime.now(timezone.utc),
            claimed_source_url="https://mohre.gov.ae/decree.pdf",
            portal_navigation_steps="Step 1, step 2, step 3, step 4.",
            capture_evidence_ref="evidence/screen_01.png",
            independent_cross_check_ref="none",
            independent_cross_check_passed=True,
            corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
        )

    # Reject navigation steps too short (< 10 chars)
    with pytest.raises(ValidationError):
        ManualAttestation(
            attestation_id="attest-short",
            source_id="src-001",
            operator_id="operator-42",
            acquired_at_utc=datetime.now(timezone.utc),
            claimed_source_url="https://mohre.gov.ae/decree.pdf",
            portal_navigation_steps="short",
            capture_evidence_ref="evidence/ref",
            independent_cross_check_ref="ref",
            independent_cross_check_passed=False,
            corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
        )


def test_legal_source_provenance_validation() -> None:
    # GOV_AUTOMATED_TLS_VERIFIED requires valid tls_cert_fingerprint
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="tls-missing-fp",
            document_id="doc-1",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="a" * 64,
            is_authoritative=False,
            extraction_method="pdf_text",
            authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
            tls_cert_fingerprint=None,
        )

    # Invalid fingerprint pattern
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="tls-bad-fp",
            document_id="doc-1",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="a" * 64,
            is_authoritative=False,
            extraction_method="pdf_text",
            authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
            tls_cert_fingerprint="not-a-fingerprint",
        )

    # OPERATOR_ATTESTED_ONLY requires attestation_id
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="op-missing-attest",
            document_id="doc-1",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="a" * 64,
            is_authoritative=False,
            extraction_method="manual",
            authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
            attestation_id=None,
        )

    # is_authoritative=True requires language="ar"
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="bad-auth-lang",
            document_id="doc-1",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="a" * 64,
            is_authoritative=True,
            extraction_method="pdf_text",
        )


def test_boundary_registry_model() -> None:
    registry = BoundaryRegistry(
        verified_topics=("employment_contract", "probation_period"),
        post_cutoff_amendments={"arbitration": "FDL_XX_2024"},
        coverage_status="COMPLETE",
    )
    assert "employment_contract" in registry.verified_topics
    assert registry.coverage_status == "COMPLETE"
