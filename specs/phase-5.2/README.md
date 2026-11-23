# LexAgent — Phase 5.2 Planning Package (GitHub Spec Kit)

**Project:** `uae-lexagent` (`https://github.com/Adithya-1508/LexAgent`)
**Milestone:** Phase 5.2 — Authoritative UAE Employment-Law Corpus Expansion, Source Provenance, Bilingual Alignment, and Amendment-Aware Legal Retrieval
**Status:** Final Repo-Grounded Planning Package — **Ready for Owner Approval (No Production Code Modified)**
**Date:** November 2026

## Package Contents

This archive is structured to drop directly into `specs/phase-5.2/` on `main` upon owner approval:

| File | Purpose |
|---|---|
| `constitution.md` | Project legal-AI constitutional invariants (Arabic primacy, fail-closed defaults, zero paid dependencies) |
| `spec.md` | Functional & safety requirements (`REQ-01`–`REQ-12`), user scenarios, scope boundaries, acceptance criteria |
| `research.md` | Primary-source legal inventory, verified UAE legislation URLs, PDF CMap defect analysis, unresolved facts |
| `plan.md` | Repo-grounded system architecture, `ADR-001`–`ADR-008`, module touchpoints, CI & security alignment |
| `data-model.md` | Canonical Pydantic/JSON contracts, enums, multi-version `as_of` semantics, migration protocol, `RV-01`–`RV-11` |
| `tasks.md` | Milestone gates (`M5.2.0`–`M5.2.5`), 9-PR strategy, `REQ -> Task -> Test` traceability, 26–36d estimates |
| `risk-register.md` | Risk matrix, redistribution/licensing protocol, rollback runbook, operational failure modes |
| `review-findings.md` | Traceable log of self-review passes, Round 1 (#1–#10), Contract Review (#1–#7), and Codex BLOCK Remediation |
| `M5.2.0_Legal_Source_Verification.md` | Exhaustive primary-source evidence matrix for FDL 33/2021, 14/2022, 20/2023, 9/2024, CR 1/2022, and Art 51(2) |
| `M5.2.0_Compatibility_Matrix.md` | Exact test-level inventory reconciling all 145 baseline tests across 4 tiers with zero double-counting |
| `M5.2.0_Implementation_Readiness.md` | Implementation readiness review, 5 quality gates (202 passed tests), and PR-2 scope authorization gate |
| `M5.2.0_Validation_Report.md` | Independent architectural audit, requirements traceability, and Draft 2020-12 schema validation evidence |
| `M5.2.0_Open_Issues.md` | Canonical issue register classifying findings into `VERIFIED`, `PROPOSED`, and `BLOCKED` |
| `contracts/*.schema.json` | Strict Draft 2020-12 JSON Schemas for all Phase 5.2 canonical artifacts with conditional checks |
| `contracts/retrieval_and_verifier_contracts.md` | Exact multi-version indexing, `as_of` selection, fail-closed disclosure, and claim-key registry contracts |
| `fixtures/*.json` | Positive and negative test fixtures marked `SYNTHETIC_TEST_ONLY` with verified SHA-256 digests |

## Owner Decisions Incorporated

1. **Cabinet Resolution No. 96 of 2023 (Voluntary EOSB Savings Scheme):** Deferred to Phase 5.3 for full ingestion; mandatory applicability caveat (`STATUTORY_INTERACTION_CR_96_2023`) attached to Article 51 provisions in Phase 5.2.
2. **Official Source Snapshots in Git:** Approved for small official HTML/PDF snapshots under `data/official_sources/snapshots/`, subject to documented redistribution permission checks and SHA-256 integrity verification.
3. **Partial Corpus Releases:** Approved; independently verified provisions may be published while unverified provisions remain quarantined and ordinary retrieval fails closed (`include_caveated=False`, `allow_legacy_unverified=False`).

## Readiness Verdict

**`READY FOR M5.2.0 IMPLEMENTATION (PR-1) UPON FINAL OWNER SIGN-OFF`**
