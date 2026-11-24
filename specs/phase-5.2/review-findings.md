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
2. **Exact Test-Level Baseline Compatibility:** Eliminated all approximate counts and double-counting in `M5.2.0_Compatibility_Matrix.md`. Reconciled all 145 baseline tests individually into 117 Tier 1 (Unchanged), 23 Tier 2 (Opt-In), 5 Tier 3 (Upgraded), and 0 Tier 4 (Failures).
3. **Claim-Level Legal Evidence Table:** Added comprehensive evidence matrix in `M5.2.0_Legal_Source_Verification.md` with explicit URLs, verbatim captured facts, verification dates, and access limitations. Gated upstream PDFs explicitly marked `UNRETRIEVED_UPSTREAM_PDF_GATE` rather than falsely claiming independent verification.
4. **Document Harmonization:** Reconciled coverage dates (2024-08-31 cutoff, 74 and 39 articles), publication defaults, and quarantine rules across all 17 planning package files and 5 baseline validation reports.
5. **CodeRabbit Status Separation:** Recorded external third-party CodeRabbit app status (`Review rate limited`) separately from the 11 passing internal GitHub Actions CI workflow jobs.

## 5. PR #2 Independent Codex BLOCK Remediation Pass (Planning Baseline Scope)

In response to independent Codex review findings on PR #2 at `56c2b21`, the planning baseline was comprehensively hardened strictly within specification, schema, and fixture artifacts (zero production runtime code modified):

1. **Git Integration & Conflict Reproduction (MEDIUM):**
   - Inspected `origin/main` (`913fb6b`), containing merged PR #3 tooling baseline (`lefthook==2.2.2`).
   - Reproduced merge conflict on `dev` dependencies in `pyproject.toml` (`lefthook==2.2.2` vs `jsonschema>=4.20.0`).
   - Resolved cleanly by combining both: pinned `lefthook==2.2.2` and updated JSON Schema to `jsonschema[format-nongpl]>=4.23.0`.
   - Preserved all 11 existing CI workflow jobs and left `src/uae_lexagent/` untouched.

2. **Official Legal-Source Verification (HIGH):**
   - **FDL 20/2023:** Verified enactment 2023-09-13, Gazette 759 (2023-09-28), commencement 2024-01-01 per Art. 2. Summary MoHRE decisions <= AED 50k appealed to Court of Appeal within **15 working days** (*خمسة عشر يوم عمل*); court delivers judgment within 15 working days; 1-year limitation from right due date. Official standalone English translation confirmed unavailable on federal portal; MoHRE-hosted English translation is strictly informational convenience and legally non-authoritative (Arabic text is sole authority per Art 66(2)).
   - **FDL 9/2024:** Verified enactment 2024-07-29, Gazette 780 (2024-07-31), commencement 2024-08-31 per Art. 4 (after 30 days from publication). Articles 1–4 verified: Art. 1 replaces Art. 54 (appeal venue to Court of First Instance within 15 working days, 2-year limitation from termination) and Art. 60 (fines up to AED 1M); Art. 2 mandates transfer of pending Court of Appeal cases without fees except decided/reserved matters; Art. 3 repeals contrary provisions; Art. 4 governs publication and 30-day commencement.
   - **Cabinet Resolution 1/2022:** Resolved discrepancy between 17 January 2022 and 3 February 2022 using primary sources: formally enacted/issued **3 February 2022** (2 Rajab 1443 AH) by the Vice President and Prime Minister per `uaelegislation.gov.ae`; 17 January 2022 was preliminary Cabinet session approval / press announcement. Published in Gazette 721 dated 2022-02-15, in force 2022-02-02 per Art. 39. Exactly 39 articles (1 ingested in baseline, 38 uningested). Primary metadata reconciled in `data/official_sources/cabinet_resolution_1_2022_document.json` (`issued_date: 2022-02-03`).
   - **Statutory Clause-Level Binding (Article 51(1) vs 51(2)):** Disentangled Article 51(1) (UAE National Worker pensions/GPSSA) from Article 51(2) (foreign full-time worker end-of-service gratuity formula and 1-year service threshold). Corrected fixtures and claim registry to bind gratuity entitlement strictly to `ART_51_02`.

3. **Fail-Closed Current-Law Contract & Freshness Horizon (HIGH):**
   - **Positive Topic Coverage Principle:** Absence of an amending decree is not proof of completeness (anti-open-world). Topics with unknown or incomplete coverage in `BoundaryRegistry` fail closed with `stale_or_incomplete_amendment_coverage`.
   - **Freshness Horizon Clock & Fail-Closed Current-Law Rule:** If runtime execution clock exceeds 180 days past cutoff (`execution_time > system_cutoff + 180 days`), all current-law queries (undated or `as_of` resolving to `system_cutoff`) MUST FAIL CLOSED and ABSTAIN (`abstention_reason="corpus_verification_expired"`). A warning alone **MUST NEVER** authorize a current-law answer. Substantive answers are permitted strictly for explicit historical queries where `as_of <= system_cutoff` with verified topic coverage.
   - **Four-Stage Enforcement:** Pre-retrieval routing, retrieval filters/tools, synthesis verifier, and agent output packaging.
   - **Dual-Path Propagation:** Caveat disclosures propagate through both `GroundedLegalAnswer.caveat_disclosures` and `LegalResponse.caveat_disclosures`.
   - **Historical Answer Overrides:** Retrospective queries for repealed provisions attach `HISTORICAL_ANSWER_CAVEAT`.

4. **Mandatory JSON Schema Validation (HIGH):**
   - Removed silent `pytest.importorskip("jsonschema")` in favor of direct import, eliminating risk of false green CI builds.
   - Pinned `jsonschema[format-nongpl]>=4.23.0` in dev dependencies, providing `rfc3339-validator` and `rfc3986-validator`.
   - Attached `format_checker=Draft202012Validator.FORMAT_CHECKER` to schema tests.
   - Expanded `tests/test_phase_5_2_schemas.py` to 61 independent unit tests covering missing required fields, invalid enums, extra properties rejection, malformed URIs, malformed datetimes, and conditional logic.

5. **Synthetic Fixtures & Provenance (HIGH):**
   - Marked all 14 schema fixtures as `SYNTHETIC_TEST_ONLY_...` to avoid confusing test payloads with real statutory records.
   - Replaced empty string SHA-256 (`e3b0c442...`) in source snapshot with real calculated digest of known nonempty synthetic bytes (`0f8ca430...`).
   - Replaced release manifest empty digest with real calculated SHA-256 (`4676bd2c...`).
   - Explicitly separated operator custody attestation from automated government authentication.

6. **Schema-to-Model Consistency (MEDIUM):**
   - Implemented Draft 2020-12 `allOf`/`if`/`then` conditionals across all 7 schemas:
     - `source_snapshot.schema.json`: Derived text requires lineage and extraction tool/version; manual tiers require attestation ID; automated TLS requires TLS cert fingerprint.
     - `manual_attestation.schema.json`: Operator-only corroboration requires `independent_cross_check_passed: false`.
     - `legal_provision.schema.json`: Arabic primacy (`is_authoritative: true` requires `language: "ar"` and `translation_provenance: "authoritative_original"`); `in_force` requires `effective_from`; `superseded` requires both `effective_from` and `effective_until`; `repealed`/`suspended_gap` requires reason; caveats require non-empty codes; verified publishable requires reviewer details.
     - `amendment_edge.schema.json`: `replaces`/`amends` requires both prior and new version IDs; `repeals` requires prior ID and `new_version_id: null`; `adds` requires `prior_version_id: null` and new ID.
     - `bilingual_alignment_audit.schema.json`: Reviewed status requires reviewer ID and timestamp; unaligned EN requires `en_provision_id: null`.

7. **Retrieval Migration & Release Sequencing (MEDIUM):**
   - Specified hybrid retrieval pipeline: BM25 keyword search, dense vector embeddings, candidate version pruning prior to Reciprocal Rank Fusion (RRF), same-version bilingual alignment collapsing, and version-scoped citation mapping.
   - Mandated that the future Phase 5.2 release pipeline enforce offline release manifest verification (`RV-01`–`RV-11` scheduled for M5.2.3) before corpus publication and tagging, alongside the existing corpus integrity checker (`python -m uae_lexagent.evaluation.validate`).
   - Positioned release manifest and approved registry inputs prior to downstream index consumption.
   - Supported `SOURCE_DATE_EPOCH` for reproducible build timestamps.

8. **Compatibility Matrix Exact Test Inventory (MEDIUM):**
   - Regenerated exact test inventory from actual pytest collection across 9 files (145 baseline tests: 117 Tier 1 Unchanged, 23 Tier 2 Opt-In, 5 Tier 3 Upgraded, 0 Failures).
   - Reconciled exact test node IDs in `tests/test_models.py` (replacing placeholder names with actual names) and `tests/test_agent.py` (restoring omitted `test_unverified_answer_never_leaks_unsupported_text`).
   - Corrected fingerprint definition to canonical JSON serialization of sorted provisions hashed with SHA-256.

9. **Implementation Readiness & Full Output Disclosure (MEDIUM):**
   - Updated readiness and validation reports to 61 schema tests and 206 total tests.
   - Inserted full, unclipped output of `validate.py`, explicitly disclosing the 4 unretrieved upstream raw PDF snapshots and 61 uningested articles of FDL 33/2021.
   - Documented Cabinet Resolution 1/2022's full 39-article scope (1 ingested, 38 uningested).

## 6. PR #2 Final Codex BLOCK Remediation Pass (Issue-by-Issue Resolution)

In response to the independent Codex re-review of PR #2 at `31b2deee7ed21805b4b4da0ccb12ff0ed9df0783` (which confirmed PR mergeability and 11 green CI jobs), all confirmed findings were remediated within specification, contract, schema, and test artifacts without modifying production runtime code (`src/uae_lexagent/`):

1. **Historical Coverage Verification (HIGH):**
   - **Defect Addressed:** Previous historical-query branch allowed interval selection on `[effective_from, effective_until]` without affirmative confirmation that all amending instruments for that topic were ingested.
   - **Remediation:** Mandated positive verified topic certification in `BoundaryRegistry.verified_topics`. Matching an existing statutory interval is strictly insufficient by itself; unknown, missing, incomplete, or contradictory historical coverage fails closed and abstains (`abstention_reason="unverified_historical_topic_coverage"`).
   - **Current-Law Freshness:** Current-law queries continue to enforce the 180-day freshness horizon from `effective_as_of_max`, failing closed with `abstention_reason="corpus_verification_expired"`.
   - **Acceptance Scenario Added:** S-10 (historical query for uncertified topic fails closed).

2. **FDL 9/2024 Article 54 Transition Operative Contract (HIGH):**
   - **Defect Addressed:** Over-simplified transitional rules risked determining venue solely by filing date.
   - **Remediation:** Detailed 5 discrete conditions required to apply the transitional rule:
     1. Matter falls within Article 54(3) summary jurisdiction;
     2. Benchmark date is 31 August 2024;
     3. Matter was pending before Court of Appeal on 31 August 2024;
     4. Decided exception (*محكوم فيها*): lawsuits already decided remain before Court of Appeal;
     5. Reserved for judgment exception (*محجوزة للحكم*): lawsuits reserved for judgment remain before Court of Appeal.
   - **Procedural Fact Sufficiency:** A court can NEVER be selected by filing date alone; missing procedural facts must trigger clarification or fail closed (`abstention_reason="missing_procedural_facts"`).
   - **Acceptance Scenarios Added:** S-11 (qualifying pending transfer to CFI without fees), S-12 (decided exception retained at CoA), S-13 (reserved for judgment exception retained at CoA), S-14 (unrelated appeal unaffected), S-15 (pre-commencement historical query), S-16 (ambiguous procedural facts abstention).

3. **Schema Safety Invariants (HIGH):**
   - Fixed 4 verified schema defects across contracts:
     - `legal_provision.schema.json`: In `VERIFIED_PUBLISHABLE` conditional block, forbade active caveats by setting `caveat_codes: { maxItems: 0 }`.
     - `legal_provision.schema.json`: When `publication_state == "UNRESOLVED_HISTORICAL"`, required explicit uncertainty bounds (`uncertainty_window_start`, `uncertainty_window_end`).
     - `bilingual_alignment_audit.schema.json`: When `structural_alignment_status == "exact_match"` or `semantic_equivalence_status == "human_reviewed_equivalent"`, required non-null, non-empty `en_provision_id` (`{ "type": "string", "minLength": 1 }`).
     - `source_snapshot.schema.json`: When `is_authoritative: true`, strictly required `language: "ar"`.
   - Added 4 focused negative unit tests in `tests/test_phase_5_2_schemas.py` asserting exact error paths and validator codes (`maxItems`, `type`, `const`).

4. **Enforcement Coverage Across Actual Repository Modules (HIGH):**
   - Updated `retrieval_and_verifier_contracts.md`, `plan.md`, and `tasks.md` to reference the real repository layout:
     - Standalone retrieval: `src/uae_lexagent/retrieval/hybrid.py`, `baseline.py`, `sparse.py`, `vector_store.py` (`InMemoryVectorStore`, `QdrantVectorStore`), `fusion.py`, `indexing.py`, `contracts.py`.
     - Synthesis engine & verifier: `src/uae_lexagent/synthesis/engine.py` (`LegalSynthesisEngine`), `synthesis/verifier.py`, `synthesis/contracts.py`.
     - Agent orchestration: `src/uae_lexagent/agent/nodes.py`, `agent/graph.py`, `agent/orchestrator.py`.
   - Mandated universal evidence provenance gate (`RV-01`–`RV-03`) for caller-supplied synthesis evidence.

5. **Medium Findings Resolved:**
   - **Synthetic Fixture Integrity:** Reconciled SHA-256 for `claim_key_registry.positive.json` in `release_manifest.positive.json`. Formally classified fixture hashes as shape-only examples with no integrity claims, completely isolated from the production publication pipeline.
   - **Compatibility Matrix Reclassification:** Reclassified `test_bm25_index_filters_jurisdiction_and_temporality` to Tier 2 (opt-in in M5.2.3 / PR-6) because its in-memory fixture provisions default to `LEGACY_UNVERIFIED`. Updated counts: 117 Tier 1, 23 Tier 2, 5 Tier 3 = 145 baseline tests; 61 schema tests = 206 total tests.
   - **Canonical Corpus Fingerprint vs Release Manifest Digest:** Formally separated the data payload fingerprint (SHA-256 of sorted canonical provisions via `compute_corpus_fingerprint`) from the release manifest signing envelope (SHA-256 of `release_manifest.json`).
   - **Evidence Reports & Status Reconciliation:** Verified FDL 20/2023 enactment date as 27 Safar 1445 AH (*27 صفر 1445 هـ* corresponding to 13 September 2023 per primary Arabic enactment text), distinguished primary statutory facts from secondary commentary, confirmed 14 ingested articles (61+38 uningested), 11 passing CI jobs, and 436 warnings in existing test runs.
