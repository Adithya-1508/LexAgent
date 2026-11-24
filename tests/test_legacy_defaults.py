"""Tests verifying fail-closed legacy defaults and publication state invariants (T-02 / REQ-06)."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from uae_lexagent.ingestion import load_provisions, load_source_manifest
from uae_lexagent.models import (
    AuthenticityTier,
    DocumentCompleteness,
    Jurisdiction,
    LegalDocument,
    LegalProvision,
    LegalSource,
    LifecycleState,
    PublicationState,
    RedistributionClearance,
    SourceType,
)


def test_legal_source_legacy_defaults() -> None:
    source = LegalSource(
        source_id="legacy-src",
        document_id="doc-1",
        language="ar",
        publisher="MOHRE",
        source_url="https://mohre.gov.ae/law.pdf",
        local_path="law.pdf",
        retrieved_at=datetime.now(timezone.utc),
        sha256="a" * 64,
        is_authoritative=True,
        extraction_method="manual",
    )
    assert source.authenticity_tier is AuthenticityTier.LEGACY_UNATTESTED
    assert source.redistribution_clearance is RedistributionClearance.PENDING_REVIEW
    assert source.source_role == "extraction_primary"
    assert source.artifact_type == "raw_pdf"
    assert source.attestation_id is None
    assert source.tls_cert_fingerprint is None


def test_legal_document_legacy_defaults() -> None:
    doc = LegalDocument(
        document_id="doc-1",
        title_en="Title",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        source_url="https://mohre.gov.ae/law",
        source_type=SourceType.OFFICIAL,
    )
    assert doc.completeness is DocumentCompleteness.PARTIAL_MVP
    assert doc.missing_article_numbers == []
    assert doc.gazette_number is None


def test_legal_provision_legacy_defaults() -> None:
    prov = LegalProvision(
        provision_id="prov-1-ar",
        document_id="doc-1",
        source_id="src-1",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        article="1",
        language="ar",
        is_authoritative=True,
        text="نص تشريعي",
        source_url="https://mohre.gov.ae/law.pdf",
    )
    assert prov.publication_state is PublicationState.LEGACY_UNVERIFIED
    assert prov.logical_provision_id == "prov-1-ar"
    assert prov.version_id == "prov-1-ar@legacy"
    assert prov.lifecycle_state is LifecycleState.IN_FORCE
    assert prov.caveat_codes == []
    assert prov.translation_provenance == "authoritative_original"
    assert prov.human_verification_status == "unreviewed"
    assert prov.cmap_corruption_detected is False


def test_loaded_official_sources_have_fail_closed_legacy_defaults() -> None:
    manifest_path = Path("data/official_sources/manifest.json")
    sources = load_source_manifest(manifest_path)
    assert len(sources) == 4
    for src in sources:
        assert src.authenticity_tier is AuthenticityTier.LEGACY_UNATTESTED
        assert src.redistribution_clearance is RedistributionClearance.PENDING_REVIEW

    provs_path = Path("data/official_sources/fdl_33_2021_provisions.json")
    provisions = load_provisions(provs_path)
    assert len(provisions) == 78
    for p in provisions:
        assert p.publication_state is PublicationState.LEGACY_UNVERIFIED
        assert p.logical_provision_id is not None
        assert p.version_id is not None


def _make_base_provision(provision_id: str = "prov-test", **kwargs: object) -> LegalProvision:
    params: dict[str, object] = {
        "provision_id": provision_id,
        "document_id": "doc-1",
        "source_id": "src-1",
        "jurisdiction": Jurisdiction.UAE_ONSHORE,
        "legal_domain": "employment",
        "article": "1",
        "language": "ar",
        "is_authoritative": True,
        "text": "نص",
        "source_url": "https://mohre.gov.ae/law.pdf",
    }
    params.update(kwargs)
    return LegalProvision.model_validate(params)


def test_publication_state_verified_publishable_invariants() -> None:
    # Valid VERIFIED_PUBLISHABLE
    valid_prov = _make_base_provision(
        "prov-verified",
        publication_state=PublicationState.VERIFIED_PUBLISHABLE,
        human_verification_status="extraction_verified",
        reviewer_id="reviewer-1",
        reviewed_at_utc=datetime.now(timezone.utc),
        caveat_codes=[],
    )
    assert valid_prov.publication_state is PublicationState.VERIFIED_PUBLISHABLE

    # Forbids active caveats
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-verified-caveat",
            publication_state=PublicationState.VERIFIED_PUBLISHABLE,
            human_verification_status="extraction_verified",
            reviewer_id="reviewer-1",
            reviewed_at_utc=datetime.now(timezone.utc),
            caveat_codes=["SOME_CAVEAT"],
        )

    # Forbids unreviewed human verification status
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-verified-unreviewed",
            publication_state=PublicationState.VERIFIED_PUBLISHABLE,
            human_verification_status="unreviewed",
            reviewer_id="reviewer-1",
            reviewed_at_utc=datetime.now(timezone.utc),
        )

    # Forbids cmap corruption detected
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-verified-cmap",
            publication_state=PublicationState.VERIFIED_PUBLISHABLE,
            human_verification_status="extraction_verified",
            reviewer_id="reviewer-1",
            reviewed_at_utc=datetime.now(timezone.utc),
            cmap_corruption_detected=True,
        )


def test_publication_state_caveat_and_historical_invariants() -> None:
    # PUBLISHED_WITH_CAVEAT requires at least 1 caveat code
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-caveated-empty",
            publication_state=PublicationState.PUBLISHED_WITH_CAVEAT,
            caveat_codes=[],
        )

    prov_caveated = _make_base_provision(
        "prov-caveated-ok",
        publication_state=PublicationState.PUBLISHED_WITH_CAVEAT,
        caveat_codes=["STATUTORY_INTERACTION_CR_96_2023"],
    )
    assert len(prov_caveated.caveat_codes) == 1

    # UNRESOLVED_HISTORICAL requires uncertainty window start and end
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-hist-none",
            publication_state=PublicationState.UNRESOLVED_HISTORICAL,
            uncertainty_window_start=None,
            uncertainty_window_end=None,
        )

    prov_historical = _make_base_provision(
        "prov-hist-ok",
        publication_state=PublicationState.UNRESOLVED_HISTORICAL,
        uncertainty_window_start=date(2022, 2, 2),
        uncertainty_window_end=date(2024, 8, 31),
    )
    assert prov_historical.uncertainty_window_start == date(2022, 2, 2)


def test_lifecycle_state_superseded_and_repealed_invariants() -> None:
    # SUPERSEDED requires effective_from and effective_until
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-sup-none",
            lifecycle_state=LifecycleState.SUPERSEDED,
            effective_from=date(2022, 2, 2),
            effective_until=None,
        )

    # REPEALED requires gap_or_repeal_reason
    with pytest.raises(ValidationError):
        _make_base_provision(
            "prov-rep-none",
            lifecycle_state=LifecycleState.REPEALED,
            gap_or_repeal_reason=None,
        )

    prov_repealed = _make_base_provision(
        "prov-rep-ok",
        lifecycle_state=LifecycleState.REPEALED,
        gap_or_repeal_reason="Repealed by Federal Decree-Law No. 20 of 2023",
    )
    assert prov_repealed.gap_or_repeal_reason is not None
    assert "Federal Decree-Law No. 20 of 2023" in prov_repealed.gap_or_repeal_reason
