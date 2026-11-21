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

## 4. PR #2 Remediation Pass (M5.2.0 Hardening)
1. **Corrected Missing-`as_of` Contract:** Defined strict temporal cutoff governance. A historical cutoff (`effective_as_of_max = 2024-08-31`) must never be silently served as current law. Mandated `TEMPORAL_CUTOFF_DISCLOSURE` on all undated queries and fail-closed abstention (`stale_or_incomplete_amendment_coverage`) for provisions with post-cutoff amendments.
2. **Exact Test-Level Baseline Compatibility:** Eliminated all approximate counts and double-counting in `M5.2.0_Compatibility_Matrix.md`. Reconciled all 145 baseline tests individually into 118 Tier 1 (Unchanged), 22 Tier 2 (Opt-In), 5 Tier 3 (Upgraded), and 0 Tier 4 (Failures).
3. **Claim-Level Legal Evidence Table:** Added comprehensive evidence matrix in `M5.2.0_Legal_Source_Verification.md` with explicit URLs, verbatim captured facts, verification dates, and access limitations. Gated upstream PDFs explicitly marked `UNRETRIEVED_UPSTREAM_PDF_GATE` rather than falsely claiming independent verification.
4. **Document Harmonization:** Reconciled coverage dates (2024-08-31 cutoff, 74 and 39 articles), publication defaults, and quarantine rules across all 17 planning package files and 5 baseline validation reports.
5. **CodeRabbit Status Separation:** Recorded external third-party CodeRabbit app status (`Review rate limited`) separately from the 11 passing internal GitHub Actions CI workflow jobs.
