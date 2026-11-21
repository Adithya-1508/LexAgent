# LexAgent Phase 5.2 — Architecture & Contract Review Log (`review-findings.md`)

This document records all structured self-review passes, Review Round 1 findings (#1–#10), repository grounding updates, and Final Contract Review resolutions (#1–#7).

## 1. Structured Self-Review Passes (Initial Draft)
- **Architecture Review:** Identified portal scraping fragility in CI -> mandated offline snapshot replay; later eliminated SQLite entirely after inspecting `main` (`ADR-001`).
- **Legal Correctness Review:** Identified loss of pre-2024 text on consolidated portal page `1541`; identified Art. 51 interaction with CR 96/2023 and 2026 WPS Ministerial Resolution 340/2026.
- **Implementation Review:** Split M5.2.3 into 3 atomic PRs (`PR-4`, `PR-5`, `PR-6`); added mandatory human review gate (`RV-03`) for OCR/CMap-flagged text.

## 2. Review Round 1 Findings (#1–#10) — Resolved
1. **Repository Grounding:** Inspected `pyproject.toml`, `models.py`, `ingestion.py`, `manifest.json`, `fdl_33_2021_document.json`, `cabinet_resolution_1_2022_provisions.json`, `retrieval/contracts.py`, `retrieval/indexing.py`, `synthesis/contracts.py`, `synthesis/verifier.py`, `evaluation/validate.py`, and `.github/workflows/ci.yml`.
2. **Bitemporal Correctness:** Renamed to **Effective-Date Versioning (Valid-Time Only)** with closed inclusive intervals matching existing code, paired with release-level immutability (`ADR-003`).
3. **Provenance Guarantees:** Decomposed into 5 orthogonal dimensions (`AuthenticityTier`, SHA-256 integrity, extraction fidelity, document completeness, human verification).
4. **Temporal Validation:** Replaced uninterrupted-timeline rule with explicit support for `REPEALED`, `ENACTED_PENDING_COMMENCEMENT`, `SUSPENDED_GAP`, and `EFFECTIVE_DATE_UNRESOLVED` (`RV-05`).
5. **Bilingual Validation:** Separated `translation_provenance`, `structural_alignment_status`, `numeric_consistency_status` (triage only), and `semantic_equivalence_status` (`RV-09`).
6. **Legal Inventory:** Revalidated against `uaelegislation.gov.ae`; flagged FDL 33/2021 date discrepancy in current repo (`2021-11-15` vs. `2021-09-20`) and marked all unresolved Gazette dates explicitly.
7. **Backward Compatibility:** Defined explicit contract test suite (`tests/test_phase5_2_compat_contracts.py`) and documented deliberate behavior deltas.
8. **Manual Acquisition Security:** Added `ManualAttestation` (`ADR-006`); prevented URL string from acting as proof of origin.
9. **Partial Releases:** Added entity-level publication states and quarantine ledgers (`ADR-007`).
10. **Task Estimates:** Revised solo-developer estimate from 15–20d to 26–36d (expected ~30d) across 9 PRs.

## 3. Final Contract Issues (#1–#7) — Resolved
1. **Fail-Closed `include_caveated`:** Changed `RetrievalFilters.include_caveated` default to `False`; caveated provisions require explicit opt-in (`include_caveated=True` + `accepted_caveat_codes` / mandatory disclosure enforcement in `CitationVerifier`).
2. **No Auto-Promotion of Legacy Records:** Changed `LegalProvision.publication_state` default from `VERIFIED_PUBLISHABLE` to `LEGACY_UNVERIFIED`; added `RetrievalFilters.allow_legacy_unverified: bool = False` so ordinary retrieval fails closed while legacy Phase 5.1 tests opt in explicitly.
3. **Exact Multi-Version Indexing & `as_of` Selection:** Specified `version_map` (`version_id`), `versions_by_logical_id` (`logical_provision_id`), legacy `alignment_map` view, missing-date binding to `effective_as_of_max`, fail-closed overlap (`ambiguous_temporal_overlap`), and uncertainty-window abstention (`temporal_uncertainty`).
4. **Decoupled Document Completeness vs. Provision Eligibility:** Moved completeness exclusively to `LegalDocument.completeness` (`DocumentCompleteness` enum) and removed `INCOMPLETE_INSTRUMENT` from `PublicationState`.
5. **Operator Attestation vs. Government Authenticity:** Split `AuthenticityTier` into `GOV_AUTOMATED_TLS_VERIFIED`, `GOV_INDEPENDENTLY_CORROBORATED`, `OPERATOR_ATTESTED_ONLY`, and `LEGACY_UNATTESTED`; `OPERATOR_ATTESTED_ONLY` provisions carry `OPERATOR_ATTESTED_SOURCE_ONLY` caveat and cannot claim independent government verification.
6. **Fail-Closed Claim-Key Registry:** Specified checksummed `claim_key_registry.json` (`RV-11`) requiring explicit `approved` review status and version scoping (`applicable_version_ids`); new provisions authorize zero claims by default.
7. **End-to-End Consistency Audit:** Verified zero contradictions across defaults, enums, quarantine rules, partial releases, and Phase 5.1 compatibility contracts.
