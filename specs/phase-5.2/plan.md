# LexAgent Phase 5.2 — Architecture & Implementation Plan (`plan.md`)

## 1. System Architecture & Boundaries

Phase 5.2 decouples the **offline corpus build pipeline** (`src/uae_lexagent/corpus/`, optional `[corpus]` extra) from the **core runtime** (`models.py`, `ingestion.py`, `retrieval/`, `synthesis/`, `evaluation/`). Online runtime and CI validation read immutable JSON/JSONL artifacts and `release_manifest.json` using only `pydantic>=2.7,<3` (preserving the `core-minimal` CI job).

```text
[Official UAE Portals: uaelegislation.gov.ae / mohre.gov.ae]
       │  (Automated TLS Fetcher  OR  Attested Manual Drop-In)
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 1: Source Acquisition, Redistribution Check & Provenance  │
│ • data/official_sources/snapshots/<sha256[:2]>/<sha256>.<ext>   │
│ • data/official_sources/manifest.json (LegalSource records)     │
│ • data/official_sources/attestations.json (ManualAttestation)   │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 2: HTML-First Deterministic Extraction & Arabic Fidelity  │
│ • HTML DOM extractor (primary) / PDF+OCR fallback (flagged)     │
│ • Dual-track Arabic: verbatim `text` + search `normalized_text` │
│ • CMap corruption detector + human review queue                 │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 3: Effective-Date Versioning, Alignment & Claim Registry  │
│ • Closed inclusive [effective_from, effective_until] versions   │
│ • AmendmentEdge graph (14/2022, 20/2023, 9/2024)                │
│ • Separated BilingualAlignmentAudit records                     │
│ • Version-scoped, human-reviewed claim_key_registry.json        │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 4: Unified Validation & Quarantine Gate (RV-01..RV-11)    │
│ • Shared engine in ingestion.py + evaluation/validate.py        │
│ • Release-blocking vs. entity-quarantining enforcement          │
│ • Decoupled DocumentCompleteness vs. Provision PublicationState │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 5: Reproducible Corpus Publication                        │
│ • data/official_sources/release_manifest.json (all SHA-256s,    │
│   effective_as_of_max, published/caveated/quarantined ledgers)  │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
[Multi-Version RetrievalIndex]
       │
       ▼
[Candidate Version Pruning & Same-Version Bilingual Alignment]
       │
       ▼
[Hybrid Fusion (BM25 + Vector + Reciprocal Rank Fusion)]
       │
       ▼
[Fail-Closed RetrievalFilters & BoundaryRegistry Scope Checks]
       │
       ▼
[Data-Driven CitationVerifier (Version-Scoped Claim Authorization)]
       │
       ▼
[Dual-Path Caveat/Disclosure Propagation (GroundedLegalAnswer & LegalResponse)]
```

## 2. Architecture Decision Records (ADRs)

### ADR-001: Canonical JSON/JSONL + Release Manifest (No SQLite)
- **Alternatives:** (A) Introduce SQLite `corpus.db` compiled from JSONL; (B) **Retain Git-tracked canonical JSON/JSONL loaded via Pydantic v2 + `release_manifest.json`**; (C) Local PostgreSQL.
- **Comparison:** `ingestion.py`, `indexing.py`, and `validate.py` already load Pydantic-validated JSON lists cleanly with deterministic SHA-256 `corpus_fingerprint` hashing. SQLite adds cross-OS binary determinism overhead in CI without solving any current query bottleneck.
- **Decision:** **Option B.** Keep canonical JSON/JSONL + `release_manifest.json`. Place acquisition/parsing dependencies (`httpx`, `beautifulsoup4`, `lxml`, `pymupdf`, `hypothesis`) strictly in `[project.optional-dependencies].corpus` in `pyproject.toml`.

### ADR-002: HTML-First Extraction + Five-Dimension Provenance Separation
- **Alternatives:** (A) PDF-first extraction; (B) **HTML-first from `uaelegislation.gov.ae` DOM + archival PDF snapshots + flagged OCR fallback**; (C) VLM/OCR for all documents.
- **Decision:** **Option B.** Extend `LegalSource.artifact_type` to `Literal["raw_html", "raw_pdf", "derived_text"]`, add `source_role: Literal["extraction_primary", "archival_reference"]` so archival PDFs do not trigger orphan-source errors, and enforce five orthogonal provenance dimensions:
  1. **Source Authenticity (`AuthenticityTier`):** `GOV_AUTOMATED_TLS_VERIFIED` > `GOV_INDEPENDENTLY_CORROBORATED` > `OPERATOR_ATTESTED_ONLY` > `LEGACY_UNATTESTED`.
  2. **Cryptographic Integrity:** On-disk SHA-256 matches `manifest.json` (proves post-capture byte integrity only).
  3. **Extraction Fidelity:** DOM assertions, `cmap_corruption_detected`, `extraction_fidelity_score`.
  4. **Legal-Text Completeness:** Tracked independently on `LegalDocument.completeness`.
  5. **Human Verification:** Explicit `human_verification_status` + `reviewer_id` + `reviewed_at_utc`.

### ADR-003: Effective-Date Versioning (Valid-Time Only) with Closed Inclusive Intervals
- **Alternatives:** (A) Row-level bitemporal tables; (B) Mutable overwrite; (C) **Effective-Date Versioning with closed inclusive `[effective_from, effective_until]` intervals + release-level immutability**.
- **Justification:** Transaction time is already captured immutably at the release/Git-commit level (`release_manifest.json` + Git SHA). Closed inclusive intervals match verified Phase 5.1 behavior in `models.py`, `retrieval/contracts.py`, and `synthesis/verifier.py`. Supports `repealed`, `enacted_pending_commencement`, `suspended_gap`, and `effective_date_unresolved` (with explicit uncertainty windows).

### ADR-004: Multi-Layered Bilingual Alignment & Explicit `unavailable_official` Support
- **Alternatives:** (A) LLM-translate missing English decrees; (B) **Structural alignment of official texts only, separating provenance, structure, numeric triage, and human semantic equivalence**.
- **Decision:** **Option B.** Relax `ingestion.validate_bilingual_alignment()` to permit Arabic-only provisions when `translation_provenance == "unavailable_official"`. Automated numeric/clause checks serve strictly as anomaly-triage signals and can never set `semantic_equivalence_status = "human_reviewed_equivalent"`.

### ADR-005: Fail-Closed Backward Compatibility & Explicit Legacy Migration
- **Alternatives:** (A) Auto-default legacy provisions to `VERIFIED_PUBLISHABLE`; (B) **Default legacy provisions to `LEGACY_UNVERIFIED` and fail closed in ordinary retrieval (`include_caveated=False`, `allow_legacy_unverified=False`), with explicit opt-in for Phase 5.1 legacy fixture tests**.
- **Decision:** **Option B.** Eliminates silent promotion of unverified Phase 5.1 fixtures while keeping all existing Phase 5.1 models and tests backward-compatible under `ConfigDict(extra="forbid")`.

### ADR-006: Manual Acquisition Security & Redistribution Clearance
- **Alternatives:** (A) Trust `source_url` domain allowlist + SHA-256; (B) **Require `ManualAttestation`, distinct `OPERATOR_ATTESTED_ONLY` tier, and documented redistribution clearance before Git commit**.
- **Decision:** **Option B.** An official-looking URL on a local file is untrusted metadata. Provisions backed solely by `OPERATOR_ATTESTED_ONLY` cannot be misrepresented as independently verified government captures; unless independently corroborated (`GOV_INDEPENDENTLY_CORROBORATED`), they carry caveat `OPERATOR_ATTESTED_SOURCE_ONLY` (`PUBLISHED_WITH_CAVEAT`).

### ADR-007: Decoupled Document Completeness, Partial Releases & Fail-Closed Claim Registry
- **Alternatives:** (A) All-or-nothing corpus release + hardcoded `_ALIGNMENT_CLAIM_KEYS`; (B) **Separate `DocumentCompleteness` from `PublicationState`, enable partial releases with quarantine ledgers, and replace `_ALIGNMENT_CLAIM_KEYS` with a checksummed, fail-closed `claim_key_registry.json`**.
- **Decision:** **Option B.** Allows independently verified articles to ship safely while quarantining defects and ensuring new provisions authorize zero claims until human-reviewed entries are added.

### ADR-008: Fail-Closed Current-Law Boundary, Freshness Horizon & Dual-Path Disclosure
- **Alternatives:** (A) Open-world assumption (assume unmentioned topics have not been amended); (B) Silent temporal clamping without disclosure; (C) **Closed-world positive topic coverage, 180-day freshness horizon clock, and dual-path mandatory disclosure propagation**.
- **Decision:** **Option C.** Absence of an amending decree in the corpus cannot be treated as evidence that the law has not changed. A legal topic cannot be served as "current law" without positive confirmation that all amending instruments up to `system_cutoff` (2024-08-31) are fully ingested and verified. If topic coverage is unknown or incomplete in `BoundaryRegistry`, retrieval fails closed with explicit abstention (`stale_or_incomplete_amendment_coverage`). When `as_of` is omitted on user queries, it resolves tentatively to `system_cutoff = 2024-08-31` with mandatory `TEMPORAL_CUTOFF_DISCLOSURE`. If query execution time exceeds 180 days past `system_cutoff`, the system MUST fail closed and abstain on current-law questions (`abstention_reason="corpus_verification_expired"`). A warning alone **MUST NEVER** authorize a current-law answer. Substantive answers are strictly permitted only for explicit historical queries where `as_of <= system_cutoff` and topic coverage is verified. Disclosures propagate through both `GroundedLegalAnswer.caveat_disclosures` and `LegalResponse.caveat_disclosures`.

## 3. Repository Module Touchpoints

| Path | Change Type | Summary |
|---|---|---|
| `pyproject.toml` | Modify | Add optional `[corpus]` extra (`httpx`, `beautifulsoup4`, `lxml`, `pymupdf`, `hypothesis`); keep core dependencies strictly `pydantic>=2.7,<3` |
| `src/uae_lexagent/models.py` | Modify | Add enums (`AuthenticityTier`, `DocumentCompleteness`, `PublicationState`, `LifecycleState`, `BoundaryStatus`); extend `LegalSource`, `LegalDocument`, `LegalProvision` with fail-closed defaults; add `ManualAttestation`, `AmendmentEdge`, `BilingualAlignmentAudit`, `ClaimKeyRegistryEntry`, `BoundaryRegistryEntry`, `CorpusReleaseManifest` |
| `src/uae_lexagent/corpus/` | Add (new subpackage) | `acquisition.py`, `extractor.py`, `normalizer.py`, `aligner.py`, `release.py` (offline pipeline; supports `SOURCE_DATE_EPOCH` reproducible build timestamps; never imported by core runtime unless invoked) |
| `src/uae_lexagent/ingestion.py` | Modify | Unify `RV-01`–`RV-11` validation engine; support `.html` snapshots, `archival_reference`, `ManualAttestation`, `unavailable_official`, same-version bilingual alignment, temporal overlap/gap checks |
| `src/uae_lexagent/evaluation/validate.py` | Modify | Remove hardcoded 2-instrument file list; read `release_manifest.json`; delegate core checks to unified validator; mandate offline release verification before corpus publication/tagging; print granular publication & quarantine ledger |
| `src/uae_lexagent/retrieval/contracts.py` | Modify | Add `include_caveated: bool = False`, `allow_legacy_unverified: bool = False`, `accepted_caveat_codes: tuple[str, ...] = ()` to `RetrievalFilters`; enforce exact `as_of`, BoundaryRegistry scope checks, & quarantine abstentions |
| `src/uae_lexagent/retrieval/indexing.py` | Modify | Multi-version `version_map` & `versions_by_logical_id`; exclude `UNRESOLVED_HISTORICAL` and `BLOCKED_LEGAL_TOPIC` from dense/sparse stores while retaining `quarantine_registry` for explicit abstention; prune candidate versions prior to rank fusion; enforce same-version bilingual alignment collapsing |
| `src/uae_lexagent/retrieval/retriever.py` | Modify | Hybrid retrieval combining BM25 keyword search, dense vector similarity, and Reciprocal Rank Fusion (RRF); version candidate pruning before fusion; version-scoped citation mapping |
| `src/uae_lexagent/synthesis/contracts.py` | Modify | Add `caveat_disclosures: list[str] = Field(default_factory=list)` and `resolved_as_of: date | None = None` to `GroundedLegalAnswer` |
| `src/uae_lexagent/synthesis/verifier.py` | Modify | Load checksummed `claim_key_registry.json` (fail-closed); version-scoped citation resolution; enforce mandatory caveat disclosure and historical answer caveats |
| `src/uae_lexagent/agent.py` | Modify | Propagate caveat disclosures to `LegalResponse.caveat_disclosures`; enforce BoundaryRegistry pre-retrieval routing abstentions and post-synthesis disclosure validation |
| `.github/workflows/ci.yml` | Modify | Add offline manifest/claim-registry tamper check and `[corpus]` optional test matrix while preserving all 8 existing jobs (`bandit`, `pip-audit`, `core-minimal`, etc.) |
