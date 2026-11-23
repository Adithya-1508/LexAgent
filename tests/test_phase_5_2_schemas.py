"""Phase 5.2 JSON Schema Compliance and Fixture Validation Test Suite.

Verifies:
1. All Phase 5.2 JSON Schemas in `specs/phase-5.2/contracts/` strictly adhere to Draft 2020-12.
2. Canonical positive synthetic fixtures pass schema validation with working format checkers.
3. Negative fixtures are deterministically rejected with expected error types.
4. Independent validation tests for missing required fields, invalid enums, extra properties,
   malformed URIs, and malformed date/date-time fields.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_DIR.parent
SCHEMA_DIR = PROJECT_ROOT / "specs" / "phase-5.2" / "contracts"
FIXTURE_DIR = PROJECT_ROOT / "specs" / "phase-5.2" / "fixtures"

SCHEMA_FIXTURE_PAIRS = [
    ("amendment_edge.schema.json", "amendment_edge.positive.json", "amendment_edge.negative.json"),
    (
        "bilingual_alignment_audit.schema.json",
        "bilingual_alignment_audit.positive.json",
        "bilingual_alignment_audit.negative.json",
    ),
    (
        "claim_key_registry.schema.json",
        "claim_key_registry.positive.json",
        "claim_key_registry.negative.json",
    ),
    (
        "legal_provision.schema.json",
        "legal_provision.positive.json",
        "legal_provision.negative.json",
    ),
    (
        "manual_attestation.schema.json",
        "manual_attestation.positive.json",
        "manual_attestation.negative.json",
    ),
    (
        "release_manifest.schema.json",
        "release_manifest.positive.json",
        "release_manifest.negative.json",
    ),
    (
        "source_snapshot.schema.json",
        "source_snapshot.positive.json",
        "source_snapshot.negative.json",
    ),
]


def _load_schema(schema_filename: str) -> dict:
    schema_path = SCHEMA_DIR / schema_filename
    assert schema_path.exists(), f"Schema file not found: {schema_path}"
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_fixture(fixture_filename: str) -> dict:
    fixture_path = FIXTURE_DIR / fixture_filename
    assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"
    with fixture_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("schema_filename, _, __", SCHEMA_FIXTURE_PAIRS)
def test_schema_validity_draft_2020_12(schema_filename: str, _: str, __: str) -> None:
    """Verify that every contract schema is syntactically valid under Draft 2020-12."""
    schema = _load_schema(schema_filename)
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("schema_filename, pos_fixture, _", SCHEMA_FIXTURE_PAIRS)
def test_positive_fixtures_pass(schema_filename: str, pos_fixture: str, _: str) -> None:
    """Verify that every positive fixture validates cleanly with format checking enabled."""
    schema = _load_schema(schema_filename)
    instance = _load_fixture(pos_fixture)

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert not errors, f"Positive fixture {pos_fixture} unexpectedly failed validation: {errors}"


@pytest.mark.parametrize("schema_filename, _, neg_fixture", SCHEMA_FIXTURE_PAIRS)
def test_negative_fixtures_fail(schema_filename: str, _: str, neg_fixture: str) -> None:
    """Verify that every negative fixture fails validation deterministically."""
    schema = _load_schema(schema_filename)
    instance = _load_fixture(neg_fixture)

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert errors, (
        f"Negative fixture {neg_fixture} unexpectedly passed validation when it should fail."
    )


@pytest.mark.parametrize(
    "schema_filename, pos_fixture, required_field",
    [
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "source_id"),
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "document_id"),
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "sha256"),
        ("manual_attestation.schema.json", "manual_attestation.positive.json", "operator_id"),
        ("manual_attestation.schema.json", "manual_attestation.positive.json", "attestation_id"),
        ("legal_provision.schema.json", "legal_provision.positive.json", "article"),
        ("legal_provision.schema.json", "legal_provision.positive.json", "text"),
        ("amendment_edge.schema.json", "amendment_edge.positive.json", "edge_id"),
        ("amendment_edge.schema.json", "amendment_edge.positive.json", "action"),
        (
            "bilingual_alignment_audit.schema.json",
            "bilingual_alignment_audit.positive.json",
            "audit_id",
        ),
        ("release_manifest.schema.json", "release_manifest.positive.json", "release_version"),
        ("release_manifest.schema.json", "release_manifest.positive.json", "corpus_fingerprint"),
    ],
)
def test_missing_required_fields_rejected_independently(
    schema_filename: str, pos_fixture: str, required_field: str
) -> None:
    """Assert that omitting any required field fails with validator == 'required'."""
    schema = _load_schema(schema_filename)
    instance = copy.deepcopy(_load_fixture(pos_fixture))

    assert required_field in instance, f"Test setup error: {required_field} not in fixture"
    del instance[required_field]

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert any(err.validator == "required" and required_field in err.message for err in errors), (
        f"Expected 'required' error for {required_field}, got: {errors}"
    )


@pytest.mark.parametrize(
    "schema_filename, pos_fixture, enum_field",
    [
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "language"),
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "authenticity_tier"),
        ("legal_provision.schema.json", "legal_provision.positive.json", "jurisdiction"),
        ("legal_provision.schema.json", "legal_provision.positive.json", "lifecycle_state"),
        ("amendment_edge.schema.json", "amendment_edge.positive.json", "action"),
        (
            "bilingual_alignment_audit.schema.json",
            "bilingual_alignment_audit.positive.json",
            "structural_alignment_status",
        ),
    ],
)
def test_invalid_enums_rejected_independently(
    schema_filename: str, pos_fixture: str, enum_field: str
) -> None:
    """Assert that assigning an unlisted enum string fails with validator == 'enum'."""
    schema = _load_schema(schema_filename)
    instance = copy.deepcopy(_load_fixture(pos_fixture))

    instance[enum_field] = "INVALID_ENUM_STRING_VALUE"
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert any(
        err.validator == "enum" and (enum_field in err.path or not err.path) for err in errors
    ), f"Expected 'enum' error on {enum_field}, got: {errors}"


@pytest.mark.parametrize("schema_filename, pos_fixture", [p[:2] for p in SCHEMA_FIXTURE_PAIRS])
def test_extra_properties_rejected_independently(schema_filename: str, pos_fixture: str) -> None:
    """Assert that adding unmapped properties is rejected under additionalProperties: false."""
    schema = _load_schema(schema_filename)
    instance = copy.deepcopy(_load_fixture(pos_fixture))

    instance["unexpected_extra_property_for_testing"] = "malicious_or_accidental_payload"
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert any(
        err.validator in ("additionalProperties", "unevaluatedProperties") for err in errors
    ), f"Expected additionalProperties rejection for {schema_filename}, got: {errors}"


@pytest.mark.parametrize(
    "schema_filename, pos_fixture, uri_field",
    [
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "source_url"),
        (
            "manual_attestation.schema.json",
            "manual_attestation.positive.json",
            "claimed_source_url",
        ),
        ("legal_provision.schema.json", "legal_provision.positive.json", "source_url"),
    ],
)
def test_malformed_uris_rejected_independently(
    schema_filename: str, pos_fixture: str, uri_field: str
) -> None:
    """Assert that malformed URI strings fail format validation when FORMAT_CHECKER is active."""
    schema = _load_schema(schema_filename)
    instance = copy.deepcopy(_load_fixture(pos_fixture))

    instance[uri_field] = "not a valid uri string with spaces"
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert any(err.validator == "format" and err.validator_value == "uri" for err in errors), (
        f"Expected URI format error on {uri_field}, got: {errors}"
    )


@pytest.mark.parametrize(
    "schema_filename, pos_fixture, datetime_field",
    [
        ("source_snapshot.schema.json", "source_snapshot.positive.json", "retrieved_at"),
        ("manual_attestation.schema.json", "manual_attestation.positive.json", "acquired_at_utc"),
        ("legal_provision.schema.json", "legal_provision.positive.json", "reviewed_at_utc"),
        (
            "bilingual_alignment_audit.schema.json",
            "bilingual_alignment_audit.positive.json",
            "reviewed_at_utc",
        ),
        ("release_manifest.schema.json", "release_manifest.positive.json", "built_at_utc"),
    ],
)
def test_malformed_datetimes_rejected_independently(
    schema_filename: str, pos_fixture: str, datetime_field: str
) -> None:
    """Assert that malformed date-time strings fail format validation when FORMAT_CHECKER is active."""
    schema = _load_schema(schema_filename)
    instance = copy.deepcopy(_load_fixture(pos_fixture))

    instance[datetime_field] = "2026-13-45T99:99:99Z"  # Invalid date-time
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert any(
        err.validator == "format" and err.validator_value == "date-time" for err in errors
    ), f"Expected date-time format error on {datetime_field}, got: {errors}"


def test_schema_conditional_corroboration_consistency() -> None:
    """ManualAttestation: none_operator_only cannot claim independent_cross_check_passed = true."""
    schema = _load_schema("manual_attestation.schema.json")
    instance = copy.deepcopy(_load_fixture("manual_attestation.positive.json"))

    instance["corroboration_type"] = "none_operator_only"
    instance["independent_cross_check_passed"] = True

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert errors, "Expected validation failure for none_operator_only with cross_check_passed=True"


def test_schema_conditional_authoritative_language_primacy() -> None:
    """LegalProvision: is_authoritative = true strictly requires language = 'ar' (Art. 66 primacy)."""
    schema = _load_schema("legal_provision.schema.json")
    instance = copy.deepcopy(_load_fixture("legal_provision.positive.json"))

    instance["is_authoritative"] = True
    instance["language"] = "en"
    instance["translation_provenance"] = "official_government_translation"

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert errors, "Expected validation failure for is_authoritative=True with language='en'"


def test_schema_conditional_amendment_action_requirements() -> None:
    """AmendmentEdge: action 'replaces' strictly requires both prior_version_id and new_version_id."""
    schema = _load_schema("amendment_edge.schema.json")
    instance = copy.deepcopy(_load_fixture("amendment_edge.positive.json"))

    instance["action"] = "replaces"
    instance["prior_version_id"] = None

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = list(validator.iter_errors(instance))
    assert errors, "Expected validation failure for replaces action with prior_version_id=None"
