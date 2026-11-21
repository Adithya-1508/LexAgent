"""Phase 5.2 JSON Schema Compliance and Fixture Validation Test Suite.

Verifies:
1. All Phase 5.2 JSON Schemas in `specs/phase-5.2/contracts/` strictly adhere to Draft 2020-12.
2. Canonical positive fixtures pass schema validation cleanly.
3. Negative fixtures (with simulated defects, bad enums, format violations, or extra properties)
   are deterministically rejected by the Draft 2020-12 validator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

SCHEMA_DIR = Path("specs/phase-5.2/contracts")
FIXTURE_DIR = Path("specs/phase-5.2/fixtures")

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


@pytest.mark.parametrize("schema_filename, _, __", SCHEMA_FIXTURE_PAIRS)
def test_schema_validity_draft_2020_12(schema_filename: str, _: str, __: str) -> None:
    """Verify that every contract schema is syntactically valid under Draft 2020-12."""
    schema_path = SCHEMA_DIR / schema_filename
    assert schema_path.exists(), f"Schema file not found: {schema_path}"

    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    # Assert schema specifies Draft 2020-12
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"

    # Meta-schema validation
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("schema_filename, pos_fixture, _", SCHEMA_FIXTURE_PAIRS)
def test_positive_fixtures_pass(schema_filename: str, pos_fixture: str, _: str) -> None:
    """Verify that every positive fixture validates cleanly without error."""
    schema_path = SCHEMA_DIR / schema_filename
    fixture_path = FIXTURE_DIR / pos_fixture

    assert schema_path.exists(), f"Schema file not found: {schema_path}"
    assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    with fixture_path.open("r", encoding="utf-8") as f:
        instance = json.load(f)

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(instance))
    assert not errors, f"Positive fixture {pos_fixture} unexpectedly failed validation: {errors}"


@pytest.mark.parametrize("schema_filename, _, neg_fixture", SCHEMA_FIXTURE_PAIRS)
def test_negative_fixtures_fail(schema_filename: str, _: str, neg_fixture: str) -> None:
    """Verify that every negative fixture fails validation deterministically."""
    schema_path = SCHEMA_DIR / schema_filename
    fixture_path = FIXTURE_DIR / neg_fixture

    assert schema_path.exists(), f"Schema file not found: {schema_path}"
    assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    with fixture_path.open("r", encoding="utf-8") as f:
        instance = json.load(f)

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(instance))
    assert errors, (
        f"Negative fixture {neg_fixture} unexpectedly passed validation when it should fail."
    )
