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
| `plan.md` | Repo-grounded system architecture, `ADR-001`–`ADR-007`, module touchpoints, CI & security alignment |
| `data-model.md` | Canonical Pydantic/JSON contracts, enums, multi-version `as_of` semantics, migration protocol, `RV-01`–`RV-11` |
| `tasks.md` | Milestone gates (`M5.2.0`–`M5.2.5`), 9-PR strategy, `REQ -> Task -> Test` traceability, 26–36d estimates |
| `risk-register.md` | Risk matrix, redistribution/licensing protocol, rollback runbook, operational failure modes |
| `review-findings.md` | Traceable log of all self-review passes, Review Round 1 (#1–#10), and Final Contract Review (#1–#7) |
| `contracts/*.schema.json` | Strict Draft 2020-12 JSON Schemas for all Phase 5.2 canonical artifacts |
| `contracts/retrieval_and_verifier_contracts.md` | Exact multi-version indexing, `as_of` selection, fail-closed disclosure, and claim-key registry contracts |

## Owner Decisions Incorporated

1. **Cabinet Resolution No. 96 of 2023 (Voluntary EOSB Savings Scheme):** Deferred to Phase 5.3 for full ingestion; mandatory applicability caveat (`STATUTORY_INTERACTION_CR_96_2023`) attached to Article 51 provisions in Phase 5.2.
2. **Official Source Snapshots in Git:** Approved for small official HTML/PDF snapshots under `data/official_sources/snapshots/`, subject to documented redistribution permission checks and SHA-256 integrity verification.
3. **Partial Corpus Releases:** Approved; independently verified provisions may be published while unverified provisions remain quarantined and ordinary retrieval fails closed (`include_caveated=False`, `allow_legacy_unverified=False`).

## Readiness Verdict

**`READY FOR M5.2.0 IMPLEMENTATION (PR-1) UPON FINAL OWNER SIGN-OFF`**
