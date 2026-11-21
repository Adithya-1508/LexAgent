# LexAgent Phase 5.2 — Specification (`spec.md`)

## 1. Objective & Scope

Expand LexAgent's statutory corpus from the Phase 5.1 `partial_mvp` state (13 of 74 articles of FDL 33/2021 and 1 article of Cabinet Resolution No. 1 of 2022, backed by 4 unattested derived `.txt` files) into a provenance-verified, bilingual, amendment-aware corpus with fail-closed retrieval, partial-release quarantine support, and version-scoped citation verification.

### In Scope (Phase 5.2)
- **Federal Decree-Law No. 33 of 2021** (full 74-article target, AR authoritative + official EN).
- **Cabinet Resolution No. 1 of 2022** (Executive Regulation, full article expansion, AR authoritative + official EN).
- **Amending Federal Decree-Laws:**
  - Federal Decree-Law No. 14 of 2022 (amending Art. 8(3)).
  - Federal Decree-Law No. 20 of 2023 (amending Art. 54, effective 01 Jan 2024).
  - Federal Decree-Law No. 9 of 2024 (amending Arts. 54 and 60, effective 31 Aug 2024 — verified in M5.2.0 via Official Gazette No. 781).
- Historical version reconstruction for amended provisions (Arts. 8(3), 54, 60) with explicit uncertainty windows where historical Gazette dates require boundary modeling.
- Mandatory applicability caveat on Article 51 referencing Cabinet Resolution No. 96 of 2023.

### Explicitly Out of Scope (Boundary Registry / Phase 5.3+)
- Full ingestion of **Cabinet Resolution No. 96 of 2023** (Voluntary Alternative EOSB Savings Scheme) — deferred to Phase 5.3 per owner decision.
- **Cabinet Resolution No. 33 of 2022** (Work Injuries Tables) and **Cabinet Resolution No. 18 of 2022** (Establishment Classification) — tracked in Boundary Registry for Phase 5.3+.
- **Ministerial Resolution No. 340 of 2026** (WPS rules effective 01 Jun 2026) — tracked in Boundary Registry so LexAgent does not assert stale WPS grace periods.
- **Excluded Jurisdictions:** DIFC Employment Law, ADGM Employment Regulations, Domestic Workers (FDL 9/2022), Federal Public-Sector HR Law.

## 2. User & System Scenarios

- **S-01 (Undated / Current-Law Query with Mandatory Disclosure):** A user asks about the limitation period for labour claims without specifying a date. LexAgent binds to `effective_as_of_max` (`2024-08-31`) after verifying that no uningested post-cutoff amendments exist in the Boundary Registry for Article 54. It retrieves the post-FDL 9/2024 version of Article 54 (2 years from termination), verifies the claim against an approved claim-key entry, and returns the grounded answer accompanied by the mandatory `TEMPORAL_CUTOFF_DISCLOSURE` explicitly stating that the advice reflects statutory text verified through `2024-08-31` and does not claim to reflect subsequent legal developments.
- **S-02 (Historical Dispute Query):** A user asks about MoHRE's claim-resolution authority `as_of=2024-03-15`. LexAgent resolves the FDL 20/2023 version of Article 54 (effective 01 Jan 2024 until superseded by FDL 9/2024), never silently serving the later 2024 Court-of-First-Instance amendment.
- **S-03 (Temporal Uncertainty Window):** A user queries Article 8(3) on a date falling inside a simulated or historical unresolved Gazette publication window. LexAgent fails closed with `abstention_reason="temporal_uncertainty"`.
- **S-04 (Caveated Provision Fail-Closed):** A user queries end-of-service gratuity (Article 51, which carries `STATUTORY_INTERACTION_CR_96_2023`). Under default `include_caveated=False`, ordinary retrieval fails closed. Under explicit opt-in (`include_caveated=True` + mandatory disclosure policy), LexAgent serves Article 51 and `CitationVerifier` enforces that the CR 96/2023 applicability caveat is rendered in the final response.
- **S-05 (Partial Corpus Release):** Cabinet Resolution No. 1 of 2022 has 25 verified articles (`VERIFIED_PUBLISHABLE`) and 10 remaining unextracted articles (`DocumentCompleteness.PARTIAL_VERIFIED`). Queries matching the 25 verified articles succeed; queries targeting the missing 10 articles fail closed and abstain.
- **S-06 (Corrupted PDF Quarantine):** An amending decree PDF exhibits Arabic CMap glyph corruption. Extraction flags `cmap_corruption_detected=True`, quarantining affected provisions as `BLOCKED_LEGAL_TOPIC` until human extraction verification (`extraction_verified`) is recorded.
- **S-07 (Stale / Post-Cutoff Amendment Abstention):** A user submits an undated query regarding wage protection rules. The retrieval engine identifies that Ministerial Resolution No. 340 of 2026 is registered in the Boundary Registry as a post-cutoff amendment not yet ingested. Rather than asserting outdated rules, the engine fails closed with `abstention_reason="stale_or_incomplete_amendment_coverage"`.

## 3. Requirements & Acceptance Criteria

| Req ID | Requirement | Acceptance Criterion |
|---|---|---|
| **REQ-01** | Five-dimension source provenance & redistribution compliance | 100% of new snapshots record `authenticity_tier`, SHA-256, `redistribution_clearance`, and (for manual files) a validated `ManualAttestation`. |
| **REQ-02** | HTML-first deterministic extraction & dual-track Arabic normalization | Deterministic re-run hash equality = 100%; `text` remains verbatim while `normalized_text` powers search; CMap corruption auto-detected. |
| **REQ-03** | Effective-date versioning & amendment lineage | Closed inclusive `[effective_from, effective_until]` windows with `AmendmentEdge` links for Arts. 8(3), 54, and 60; explicit support for repeal, gaps, and uncertainty windows. |
| **REQ-04** | Multi-version indexing & exact `as_of` selection | `RetrievalIndex` supports multiple versions per `logical_provision_id` without crashing; 100% pass on `as_of` boundary, missing-date cutoff disclosure, stale-amendment abstention, overlap, and uncertainty-window tests. |
| **REQ-05** | Separated document completeness vs. provision eligibility | `LegalDocument.completeness` is decoupled from `LegalProvision.publication_state`; partially ingested documents serve independently verified provisions while quarantining defects. |
| **REQ-06** | Fail-closed defaults for caveated & legacy records | `RetrievalFilters.include_caveated=False` and `allow_legacy_unverified=False` by default; legacy provisions load as `LEGACY_UNVERIFIED` and never auto-promote. |
| **REQ-07** | Separated bilingual validation | `translation_provenance`, `structural_alignment_status`, `numeric_consistency_status`, and `semantic_equivalence_status` stored separately; heuristics never assert semantic equivalence; explicit `unavailable_official` EN permitted without failing validation. |
| **REQ-08** | Fail-closed data-driven claim-key registry | `claim_key_registry.json` replaces hardcoded `_ALIGNMENT_CLAIM_KEYS`; new provisions have 0 authorized claims until an approved, version-scoped entry is reviewed and checksummed in `release_manifest.json`. |
| **REQ-09** | Unified release & quarantine validator (`RV-01`–`RV-11`) | `ingestion.py` and `evaluation/validate.py` share one validator engine; corruption or logical contradictions block release; entity defects quarantine cleanly. |
| **REQ-10** | Backward compatibility & contract tests | Phase 5.1 JSON fixtures validate without error; `core-minimal` CI job passes with `pydantic`-only core; explicit contract test suite verifies all deliberate behavior deltas. |
| **REQ-11** | Adversarial legal safety & abstention | 100% deterministic invariant pass rate on DIFC/ADGM traps, non-existent articles, temporal boundaries, quarantined topics, and undisclosed caveats. |
| **REQ-12** | Reproducible offline CI & versioned release | `release_manifest.json` reproduces identically offline in GitHub Actions; all 8+ CI jobs (`ruff`, `mypy`, `pytest`, `legal-safety`, `corpus-benchmark-integrity`, `dependency-isolation`, `packaging`, `bandit`, `pip-audit`) pass. |
