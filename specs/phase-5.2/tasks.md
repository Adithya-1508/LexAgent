# LexAgent Phase 5.2 — Milestone Gates, PR Strategy & Task Breakdown (`tasks.md`)

## 1. Milestone Dependency Graph

```text
[M5.2.0: Spec Kit, Primary Research, Redistribution & Schema Baseline] (PR-1)
                          │
                          ▼
[M5.2.1: Source Acquisition, Attestation & Models] (PR-2)
                          │
                          ▼
[M5.2.2: HTML-First Extraction & Arabic Fidelity] (PR-3)
                          │
                          ▼
[M5.2.3: Versioning, Alignment, Manifest Inputs & RV-01..11] (PR-4, PR-5)
                          │
                          ▼
[M5.2.3b: Multi-Version Retrieval, Fusion, Verifier & Agent Integration] (PR-6)
                          │
                          ▼
[M5.2.4: Legal Eval & Adversarial Regression] (PR-7)
                          │
                          ▼
[M5.2.5: Versioned Corpus Release, Offline Verification & CI Gates] (PR-8, PR-9)
```

---

## 2. Detailed Task Breakdown & Traceability

| Task | PR | Milestone | Description | Traces To | Verification Test(s) |
|---|---|---|---|---|---|
| **T-01** | PR-1 | M5.2.0 | Commit `specs/phase-5.2/*` package; reverify primary legal sources for FDL 20/2023, FDL 9/2024, CR 1/2022, and Art 51(2) gratuity binding; enforce Draft 2020-12 schema validation with format checking; record explicit gates for unresolved redistribution rights (`redistribution_clearance`) | REQ-01, REQ-03 | `tests/test_phase_5_2_schemas.py` (61 tests passing) |
| **T-02** | PR-2 | M5.2.1 | Add optional `[corpus]` extra to `pyproject.toml`; extend `LegalSource` & add `ManualAttestation`, `AuthenticityTier`, `DocumentCompleteness`, `PublicationState` (`default=LEGACY_UNVERIFIED`) in `models.py`. Strict rule: operator attestation or caveat is NEVER treated as independent government authentication | REQ-01, REQ-05, REQ-06 | `tests/test_models.py`, `tests/test_legacy_defaults.py` |
| **T-03** | PR-2 | M5.2.1 | Implement `src/uae_lexagent/corpus/acquisition.py` (automated TLS fetcher with cert fingerprint + `ingest-local` manual attestation CLI + SHA-256 snapshot store); gate unretrieved upstream PDFs as `UNRETRIEVED_UPSTREAM_PDF_GATE` | REQ-01 | `tests/test_acquisition.py` (incl. bit-flip & forged-URL mutation tests) |
| **T-04** | PR-3 | M5.2.2 | Implement `src/uae_lexagent/corpus/extractor.py` & `normalizer.py` (HTML DOM extractor, dual-track Arabic normalizer, CMap corruption detector, flagged PDF/OCR review queue, isolate unamended 2021 text from Gazette 712) | REQ-02 | `tests/test_extractor_determinism.py`, `tests/test_arabic_normalizer_hypothesis.py`, `tests/test_cmap_gate.py` |
| **T-05** | PR-4 | M5.2.3 | Extend `LegalProvision` & `LegalDocument` in `models.py`; add `AmendmentEdge` & `BilingualAlignmentAudit`; enforce same-version bilingual alignment invariant; reconstruct historical Arts. 8(3), 54, 60; correct FDL 33/2021 Gazette dates; attach CR 96/2023 caveat to Art. 51(2) | REQ-03, REQ-05, REQ-07 | `tests/test_temporal_versioning.py`, `tests/test_bilingual_alignment.py` |
| **T-06** | PR-5 | M5.2.3 | Implement structural AR–EN aligner (`corpus/aligner.py`) and unify `RV-01`–`RV-11` validator across `ingestion.py` and `evaluation/validate.py` (removing hardcoded 2-instrument list); produce validated `release_manifest.json` and approved `claim_key_registry.json` inputs BEFORE consumer implementation | REQ-05, REQ-07, REQ-08, REQ-09 | `tests/test_validation_rules_rv01_rv11.py` (mutation & quarantine tests) |
| **T-07** | PR-6 | M5.2.3 | Upgrade retrieval, synthesis, and agent consumer layers across actual repository modules: update `src/uae_lexagent/retrieval/indexing.py` (multi-version `version_map` + quarantine registry), standalone retrieval modules (`retrieval/hybrid.py`, `baseline.py`, `sparse.py`, `vector_store.py` with `InMemoryVectorStore.query()` and `QdrantVectorStore.query()`, `fusion.py`) with reciprocal rank fusion and candidate version pruning before ranking, `retrieval/contracts.py` (`include_caveated=False`, `allow_legacy_unverified=False`, 180-day freshness horizon check failing closed on current law without warning-only bypass, positive verified topic coverage required in `BoundaryRegistry.verified_topics`, undated query cutoff disclosure, stale post-cutoff amendment abstention), `synthesis/engine.py` (`LegalSynthesisEngine` enforcing universal evidence provenance and eligibility gate across both retrieval-driven `synthesize()` and direct `synthesize_from_evidence()`) and `synthesis/verifier.py` (data-driven `claim_key_registry.json`, citation mapping to `version_id`, caveat disclosure enforcement), and agent orchestrator (`agent/nodes.py` with `synthesis_node` deriving claim IDs strictly from verified evidence and response packaging mirroring citations and disclosures to `LegalResponse`, `agent/graph.py`, `agent/orchestrator.py`). Prohibit legacy evidence in production; confine opt-in strictly to compat tests | REQ-04, REQ-06, REQ-08, REQ-10 | `tests/test_phase5_2_compat_contracts.py`, `tests/test_claim_key_registry.py`, `tests/test_multiversion_retrieval.py` |
| **T-08** | PR-7 | M5.2.4 | Expand `tests/test_legal_safety.py` and `data/evaluation/benchmark_queries.jsonl` with temporal boundary (`T-1`, `T`, `T+1`), undated query mandatory cutoff disclosure, stale post-cutoff amendment abstention, historical coverage certification (scenario S-10), pending-case transition handling (FDL 9/2024 Art. 2, scenarios S-11 to S-18: qualifying pending transfer, decided exception, reserved for judgment exception, Art. 54(4) unresolved referral exclusion, pre-commencement historical query, ambiguous procedural facts fail-closed abstention, post-commencement filing venue, non-pending lawsuit at commencement), universal evidence entry-point scenarios (Scenarios 1 to 7), overlap, uncertainty-window, caveated fail-closed, Article 51(2) clause binding, partial-release, and DIFC/ADGM tests; run comparative benchmark & failure analysis | REQ-10, REQ-11 | `pytest -m legal_safety`, benchmark diff report |
| **T-09** | PR-8 | M5.2.5 | Implement `corpus/release.py` using `SOURCE_DATE_EPOCH` for reproducible release timestamps; publish `v5.2.0` partial/full corpus release & updated `data/evaluation/manifest.json`. Mandate offline release verification before tagging | REQ-09, REQ-12 | `tests/test_release_reproducibility.py`, manifest tamper tests |
| **T-10** | PR-9 | M5.2.5 | Update `.github/workflows/ci.yml` (offline rebuild check, `RV-01`–`RV-11` gate, `[corpus]` isolated test job, verify `core-minimal`, `bandit`, `pip-audit` green) & commit rollback runbook | REQ-10, REQ-12 | Full CI workflow run green |

---

## 3. Effort Estimates & Approval Gates (Solo Developer)

**Total Estimated Effort:** **26–36 engineering days** (Expected: **~30 days**) across **9 atomic PRs**.

| Milestone | PRs | Optimistic | Expected | Pessimistic | Mandatory Approval Gate |
|---|---|---|---|---|---|
| **M5.2.0** | PR-1 | 2 d | **3 d** | 5 d | Owner approves finalized Spec Kit, primary legal evidence matrix, Draft 2020-12 schema validation suite, and redistribution check |
| **M5.2.1** | PR-2 | 3 d | **4 d** | 5 d | Core snapshots captured; `AuthenticityTier` & `ManualAttestation` verified; `core-minimal` green |
| **M5.2.2** | PR-3 | 5 d | **6 d** | 8 d | Zero unreviewed CMap/OCR segments; 100% deterministic extraction hash |
| **M5.2.3** | PR-4, PR-5, PR-6 | 8 d | **9 d** | 10 d | Release manifest and claim-key inputs validated before consumers; owner approves alignment diff, `claim_key_registry.json`, and compat contract tests |
| **M5.2.4** | PR-7 | 5 d | **5 d** | 5 d | 0 regression vs. Phase 5.1; 100% deterministic safety invariants (incl. pending-case transition and freshness horizon); failure analysis reviewed |
| **M5.2.5** | PR-8, PR-9 | 3 d | **3 d** | 3 d | Offline release verification verified before tagging; reproducible build timestamps confirmed; all CI jobs green offline; rollback drill verified; owner approves `v5.2.0` tag |
| **TOTAL** | **9 PRs** | **26 d** | **30 d** | **36 d** | — |
