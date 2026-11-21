# LexAgent Phase 5.2 — Milestone Gates, PR Strategy & Task Breakdown (`tasks.md`)

## 1. Milestone Dependency Graph

```text
[M5.2.0: Spec Kit, Primary Research & Redistribution Check] (PR-1)
                          │
                          ▼
[M5.2.1: Source Acquisition, Attestation & Models] (PR-2)
                          │
                          ▼
[M5.2.2: HTML-First Extraction & Arabic Fidelity] (PR-3)
                          │
                          ▼
[M5.2.3: Versioning, Alignment, RV-01..11 & Compat] (PR-4, PR-5, PR-6)
                          │
                          ▼
[M5.2.4: Legal Eval & Adversarial Regression] (PR-7)
                          │
                          ▼
[M5.2.5: Versioned Corpus Release & CI Gates] (PR-8, PR-9)
```

## 2. Detailed Task Breakdown & Traceability

| Task | PR | Milestone | Description | Traces To | Verification Test(s) |
|---|---|---|---|---|---|
| **T-01** | PR-1 | M5.2.0 | Commit `specs/phase-5.2/*` package; verify Gazette dates for FDL 14/2022 & FDL 9/2024, CR 1/2022 amendment status, original 2021 text sources, and UAE portal redistribution terms | REQ-01, REQ-03 | Schema lint on `specs/phase-5.2/contracts/*.schema.json` |
| **T-02** | PR-2 | M5.2.1 | Add optional `[corpus]` extra to `pyproject.toml`; extend `LegalSource` & add `ManualAttestation`, `AuthenticityTier`, `DocumentCompleteness`, `PublicationState` (`default=LEGACY_UNVERIFIED`) in `models.py` | REQ-01, REQ-05, REQ-06 | `tests/test_models.py`, `tests/test_legacy_defaults.py` |
| **T-03** | PR-2 | M5.2.1 | Implement `src/uae_lexagent/corpus/acquisition.py` (automated TLS fetcher with cert fingerprint + `ingest-local` manual attestation CLI + SHA-256 snapshot store) | REQ-01 | `tests/test_acquisition.py` (incl. bit-flip & forged-URL mutation tests) |
| **T-04** | PR-3 | M5.2.2 | Implement `src/uae_lexagent/corpus/extractor.py` & `normalizer.py` (HTML DOM extractor, dual-track Arabic normalizer, CMap corruption detector, flagged PDF/OCR review queue) | REQ-02 | `tests/test_extractor_determinism.py`, `tests/test_arabic_normalizer_hypothesis.py`, `tests/test_cmap_gate.py` |
| **T-05** | PR-4 | M5.2.3 | Extend `LegalProvision` & `LegalDocument` in `models.py`; add `AmendmentEdge` & `BilingualAlignmentAudit`; reconstruct historical Arts. 8(3), 54, 60; correct FDL 33/2021 Gazette dates (`2021-09-20`/`2021-09-26`); attach CR 96/2023 caveat to Art. 51 | REQ-03, REQ-05 | `tests/test_temporal_versioning.py` |
| **T-06** | PR-5 | M5.2.3 | Implement structural AR–EN aligner (`corpus/aligner.py`) and unify `RV-01`–`RV-11` validator across `ingestion.py` and `evaluation/validate.py` (removing hardcoded 2-instrument list) | REQ-05, REQ-07, REQ-09 | `tests/test_validation_rules_rv01_rv11.py` (mutation & quarantine tests) |
| **T-07** | PR-6 | M5.2.3 | Update `retrieval/indexing.py` (multi-version `version_map` + quarantine registry), `retrieval/contracts.py` (`include_caveated=False`, `allow_legacy_unverified=False`, exact `as_of` selection), and `synthesis/verifier.py` (fail-closed `claim_key_registry.json` + caveat disclosure enforcement) | REQ-04, REQ-06, REQ-08, REQ-10 | `tests/test_phase5_2_compat_contracts.py`, `tests/test_claim_key_registry.py`, `tests/test_multiversion_retrieval.py` |
| **T-08** | PR-7 | M5.2.4 | Expand `tests/test_legal_safety.py` and `data/evaluation/benchmark_queries.jsonl` with temporal boundary (`T-1`, `T`, `T+1`), missing `as_of`, overlap, uncertainty-window, caveated fail-closed, partial-release, and DIFC/ADGM tests; run comparative benchmark & failure analysis | REQ-10, REQ-11 | `pytest -m legal_safety`, benchmark diff report |
| **T-09** | PR-8 | M5.2.5 | Implement `corpus/release.py` to generate deterministic `release_manifest.json` & `claim_key_registry.json` checksums; publish `v5.2.0` partial/full corpus release & updated `data/evaluation/manifest.json` | REQ-09, REQ-12 | `tests/test_release_reproducibility.py`, manifest tamper tests |
| **T-10** | PR-9 | M5.2.5 | Update `.github/workflows/ci.yml` (offline rebuild check, `RV-01`–`RV-11` gate, `[corpus]` isolated test job, verify `core-minimal`, `bandit`, `pip-audit` green) & commit rollback runbook | REQ-10, REQ-12 | Full CI workflow run green |

## 3. Effort Estimates & Approval Gates (Solo Developer)

**Total Estimated Effort:** **26–36 engineering days** (Expected: **~30 days**) across **9 atomic PRs**.

| Milestone | PRs | Optimistic | Expected | Pessimistic | Mandatory Approval Gate |
|---|---|---|---|---|---|
| **M5.2.0** | PR-1 | 2 d | **3 d** | 5 d | Owner approves finalized Spec Kit, Gazette dates & redistribution check |
| **M5.2.1** | PR-2 | 3 d | **4 d** | 5 d | Core snapshots captured; `AuthenticityTier` & `ManualAttestation` verified; `core-minimal` green |
| **M5.2.2** | PR-3 | 5 d | **6 d** | 8 d | Zero unreviewed CMap/OCR segments; 100% deterministic extraction hash |
| **M5.2.3** | PR-4, PR-5, PR-6 | 8 d | **9 d** | 10 d | Owner approves alignment diff, `claim_key_registry.json`, and compat contract tests |
| **M5.2.4** | PR-7 | 5 d | **5 d** | 5 d | 0 regression vs. Phase 5.1; 100% deterministic safety invariants; failure analysis reviewed |
| **M5.2.5** | PR-8, PR-9 | 3 d | **3 d** | 3 d | All CI jobs green offline; rollback drill verified; owner approves `v5.2.0` tag |
| **TOTAL** | **9 PRs** | **26 d** | **30 d** | **36 d** | — |
