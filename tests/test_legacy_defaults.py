"""Tests verifying fail-closed legacy defaults and publication state invariants (T-02 / REQ-06)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from uae_lexagent.ingestion import compute_corpus_fingerprint, load_provisions, load_source_manifest
from uae_lexagent.models import (
    AuthenticityTier,
    DocumentCompleteness,
    Jurisdiction,
    LegalDocument,
    LegalProvision,
    LegalSource,
    LifecycleState,
    ManualAttestation,
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
    assert not prov.caveat_codes
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
        "effective_from": date(2022, 2, 2),
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


@pytest.mark.parametrize(
    ("schema_name", "model"),
    [
        ("manual_attestation", ManualAttestation),
        ("source_snapshot", LegalSource),
        ("legal_provision", LegalProvision),
    ],
)
def test_phase_5_2_model_schema_fixture_parity(schema_name: str, model: type[BaseModel]) -> None:
    root = Path("specs/phase-5.2")
    schema = json.loads(
        (root / "contracts" / f"{schema_name}.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    valid = (root / "fixtures" / f"{schema_name}.positive.json").read_text(encoding="utf-8")
    invalid = (root / "fixtures" / f"{schema_name}.negative.json").read_text(encoding="utf-8")
    assert validator.is_valid(json.loads(valid))
    assert validator.is_valid(model.model_validate_json(valid).model_dump(mode="json"))
    assert not validator.is_valid(json.loads(invalid))
    with pytest.raises(ValidationError):
        model.model_validate_json(invalid)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("document_id", None),
        ("captured_sha256", "not-a-digest"),
        ("acquired_at_utc", "2026-11-20T10:00:00"),
        ("claimed_source_url", "/relative"),
        ("independent_cross_check_passed", "false"),
    ],
)
def test_attestation_rejects_invalid_binding_fields(name: str, value: object) -> None:
    path = Path("specs/phase-5.2/fixtures/manual_attestation.positive.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[name] = value
    with pytest.raises(ValidationError):
        ManualAttestation.model_validate(payload)


def test_rejected_assignment_preserves_model_invariants() -> None:
    source = load_source_manifest(Path("data/official_sources/manifest.json"))[0]
    provision = _make_base_provision()
    with pytest.raises(ValidationError):
        source.authenticity_tier = AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED
    with pytest.raises(ValidationError):
        provision.publication_state = PublicationState.VERIFIED_PUBLISHABLE
    with pytest.raises(ValidationError):
        provision.reviewed_at_utc = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        provision.language = "en"
    assert source.authenticity_tier is AuthenticityTier.LEGACY_UNATTESTED
    assert provision.publication_state is PublicationState.LEGACY_UNVERIFIED
    assert provision.reviewed_at_utc is None
    assert provision.language == "ar"


def test_provenance_serializes_direct_nested_and_json_without_loss() -> None:
    provision = _make_base_provision(
        publication_state="LEGACY_UNVERIFIED",
        lifecycle_state="in_force",
        logical_provision_id="logical",
        normalized_text="normalized",
        extraction_fidelity_score=0.95,
        cmap_corruption_detected=False,
        reviewed_at_utc="2026-11-20T10:00:00Z",
        caveat_codes=[],
    )

    class Wrapper(BaseModel):
        provision: LegalProvision

    direct = provision.model_dump(mode="json")
    assert direct == json.loads(provision.model_dump_json())
    assert direct == Wrapper(provision=provision).model_dump(mode="json")["provision"]
    assert direct == json.loads(Wrapper(provision=provision).model_dump_json())["provision"]
    assert direct["publication_state"] == "LEGACY_UNVERIFIED"
    assert direct["cmap_corruption_detected"] is False
    assert direct["caveat_codes"] == []
    assert direct["normalized_text"] == "normalized"
    assert direct["extraction_fidelity_score"] == 0.95
    assert direct["reviewed_at_utc"] == "2026-11-20T10:00:00Z"
    assert direct["logical_provision_id"] == "logical"


def test_full_fingerprint_binds_phase_5_2_metadata() -> None:
    legacy = _make_base_provision()
    explicit_default = _make_base_provision(publication_state="LEGACY_UNVERIFIED")
    with_metadata = _make_base_provision(normalized_text="new metadata")
    assert compute_corpus_fingerprint(
        [legacy], legacy_compatible=True
    ) != compute_corpus_fingerprint([explicit_default], legacy_compatible=True)
    full = compute_corpus_fingerprint([legacy])
    assert full == compute_corpus_fingerprint([explicit_default], legacy_compatible=True)
    assert full != compute_corpus_fingerprint([with_metadata])


def test_in_force_nonlegacy_requires_effective_from() -> None:
    with pytest.raises(ValidationError, match="effective_from"):
        _make_base_provision(
            effective_from=None,
            publication_state="PUBLISHED_WITH_CAVEAT",
            caveat_codes=["TEST_CAVEAT"],
        )
