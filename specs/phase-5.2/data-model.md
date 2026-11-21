# LexAgent Phase 5.2 — Canonical Data Model & Validation Rules (`data-model.md`)

## 1. Core Enums (Zero Ambiguity)

### `AuthenticityTier` (on `LegalSource`)
- `GOV_AUTOMATED_TLS_VERIFIED`: Automated HTTPS capture from allowlisted `*.gov.ae` host with recorded TLS certificate fingerprint, HTTP status, headers, and SHA-256.
- `GOV_INDEPENDENTLY_CORROBORATED`: Manually captured artifact corroborated by an independent government verification mechanism (byte-match against automated mirror, verified UAE government PDF digital signature, or automated portal cross-check).
- `OPERATOR_ATTESTED_ONLY`: Manually supplied file with complete `ManualAttestation`, lacking automated TLS or cryptographic government signature proof.
- `LEGACY_UNATTESTED`: Default for pre-Phase 5.2 derived `.txt` sources without upstream raw snapshots.

### `DocumentCompleteness` (on `LegalDocument` ONLY)
- `COMPLETE`: 100% of statutory articles extracted and accounted for.
- `PARTIAL_VERIFIED`: Subset of articles extracted and verified; missing article numbers explicitly listed. Independently verified sibling provisions remain eligible for publication.
- `PARTIAL_MVP`: Legacy Phase 5.1 partial coverage state.
- `UNINGESTED`: Registered in Boundary Registry or target list; zero provisions ingested.

### `PublicationState` (on `LegalProvision` ONLY)
- `VERIFIED_PUBLISHABLE`: Passes `RV-01`–`RV-11` with `GOV_AUTOMATED_TLS_VERIFIED` or `GOV_INDEPENDENTLY_CORROBORATED` authenticity and no active caveats. Served by default in ordinary retrieval.
- `PUBLISHED_WITH_CAVEAT`: Authoritative text verified, but carries >=1 mandatory `caveat_codes` (e.g., `STATUTORY_INTERACTION_CR_96_2023`, `OFFICIAL_EN_UNAVAILABLE`, `OPERATOR_ATTESTED_SOURCE_ONLY`). Excluded from ordinary retrieval by default (`include_caveated=False`); requires explicit opt-in and disclosure enforcement.
- `LEGACY_UNVERIFIED`: **Default** for Phase 5.1 records loaded without Phase 5.2 migration. Excluded from ordinary retrieval by default (`allow_legacy_unverified=False`). Never auto-promoted.
- `UNRESOLVED_HISTORICAL`: Historical version with unresolved text or Gazette effective date (`uncertainty_window_start`/`end` set). Excluded from dense/sparse index; triggers `temporal_uncertainty` abstention.
- `BLOCKED_LEGAL_TOPIC`: Critical defect (CMap corruption, unreviewed OCR, failed attestation, jurisdiction violation). Hard-excluded from index; triggers `blocked_legal_topic` abstention.

### `LifecycleState` (on `LegalProvision` ONLY)
- `IN_FORCE`, `SUPERSEDED`, `REPEALED`, `ENACTED_PENDING_COMMENCEMENT`, `EFFECTIVE_DATE_UNRESOLVED`, `SUSPENDED_GAP`.

## 2. Backward-Compatible Model Extensions (`src/uae_lexagent/models.py`)

All existing models retain `ConfigDict(extra="forbid")`. Every new field has a safe, fail-closed default so Phase 5.1 JSON fixtures load without breaking changes:

- **`LegalSource` additions:**
  - `artifact_type: Literal["raw_html", "raw_pdf", "derived_text"] = "raw_pdf"`
  - `extraction_method: Literal["manual", "html_dom", "pdf_text", "ocr"]`
  - `source_role: Literal["extraction_primary", "archival_reference"] = "extraction_primary"`
  - `authenticity_tier: AuthenticityTier = AuthenticityTier.LEGACY_UNATTESTED`
  - `attestation_id: str | None = None`
  - `tls_cert_fingerprint: str | None = None`
  - `redistribution_clearance: Literal["verified_public_law", "restricted_do_not_commit", "pending_review"] = "pending_review"`
- **`LegalDocument` additions:**
  - `gazette_number: str | None = None`
  - `completeness: DocumentCompleteness = DocumentCompleteness.PARTIAL_MVP`
  - `missing_article_numbers: list[str] = Field(default_factory=list)`
- **`LegalProvision` additions:**
  - `logical_provision_id: str | None = None` (validator defaults to `alignment_id` if omitted)
  - `version_id: str | None = None` (validator defaults to `f"{alignment_id}@legacy"` if omitted)
  - `normalized_text: str | None = None`
  - `lifecycle_state: LifecycleState = LifecycleState.IN_FORCE`
  - `uncertainty_window_start: date | None = None`
  - `uncertainty_window_end: date | None = None`
  - `gap_or_repeal_reason: str | None = None`
  - `publication_state: PublicationState = PublicationState.LEGACY_UNVERIFIED` *(FAIL CLOSED)*
  - `caveat_codes: list[str] = Field(default_factory=list)`
  - `translation_provenance: Literal["authoritative_original", "official_government_translation", "unavailable_official"] | None = None`
  - `extraction_fidelity_score: float | None = None`
  - `cmap_corruption_detected: bool = False`
  - `human_verification_status: Literal["unreviewed", "extraction_verified", "rejected"] = "unreviewed"`
  - `reviewer_id: str | None = None`
  - `reviewed_at_utc: datetime | None = None`

## 3. Legacy Migration Protocol & Temporal Cutoff Governance

### 3.1 Temporal Cutoff & Current-Law Governance (`effective_as_of_max`)
1. **Four Temporal Dimensions:**
   - `release_manifest.built_at_utc`: Generation timestamp of the corpus release artifact.
   - `release_manifest.effective_as_of_max`: Upper bound statutory date verified against the Official Gazette (e.g. `2024-08-31`).
   - `LegalDocument.last_verified_at_utc` / `LegalProvision.reviewed_at_utc`: Entity-level audit timestamps.
   - `query.as_of`: Query-specified evaluation date.
2. **Current-Law Query Handling (`query.as_of is None`):**
   - The engine tentatively binds to `effective_as_of_max`.
   - **Mandatory Disclosure:** Must include `TEMPORAL_CUTOFF_DISCLOSURE` stating that advice is verified through `effective_as_of_max` and does not assert unverified post-cutoff law.
   - **Known Amendment Abstention:** If the query topic touches an instrument with post-cutoff amendments registered in the `BoundaryRegistry`, the engine fails closed with `abstention_reason="stale_or_incomplete_amendment_coverage"`.
   - **Future Date Abstention:** Queries specifying `as_of > effective_as_of_max` strictly fail closed with `abstention_reason="temporal_uncertainty"`.

### 3.2 Legacy Migration Protocol (Zero Automatic Promotion)

1. Existing Phase 5.1 files (`fdl_33_2021_provisions.json`, `cabinet_resolution_1_2022_provisions.json`, `manifest.json`) load with `publication_state = LEGACY_UNVERIFIED` and `authenticity_tier = LEGACY_UNATTESTED`.
2. Production retrieval (`allow_legacy_unverified=False`) refuses to serve `LEGACY_UNVERIFIED` records.
3. Legacy Phase 5.1 unit tests pass via explicit test-fixture opt-in (`allow_legacy_unverified=True`), preserving regression coverage without falsifying provenance.
4. To migrate any provision to `VERIFIED_PUBLISHABLE` or `PUBLISHED_WITH_CAVEAT`, the offline migration pipeline must: (a) capture official HTML/PDF snapshots with `redistribution_clearance="verified_public_law"`, (b) pass `RV-01`–`RV-11`, (c) record `human_verification_status="extraction_verified"` where required, and (d) emit a new `release_manifest.json`.

## 4. Unified Validation Rules (`RV-01`–`RV-11`)

| Rule | Dimension | Deterministic Check | Failure Action |
|---|---|---|---|
| **RV-01** | Cryptographic Integrity & Path Safety | Every `LegalSource` file exists within `base_dir` (no path traversal) and matches its recorded SHA-256. | **RELEASE-BLOCKING** |
| **RV-02** | Source Authenticity & Redistribution | Any `OPERATOR_ATTESTED_ONLY` or `GOV_INDEPENDENTLY_CORROBORATED` manual source requires a valid `ManualAttestation`. `OPERATOR_ATTESTED_ONLY` provisions MUST carry `OPERATOR_ATTESTED_SOURCE_ONLY` (`PUBLISHED_WITH_CAVEAT`), never `VERIFIED_PUBLISHABLE`. Any source with `redistribution_clearance="restricted_do_not_commit"` cannot be committed to Git. | **RELEASE-BLOCKING** (redistribution violation) / **QUARANTINE** (`BLOCKED_LEGAL_TOPIC` if attestation missing) |
| **RV-03** | Extraction Fidelity | Any provision with `cmap_corruption_detected=True`, `extraction_method="ocr"`, or `extraction_fidelity_score < 0.95` requires `human_verification_status == "extraction_verified"`. | **QUARANTINE** (`BLOCKED_LEGAL_TOPIC`) |
| **RV-04** | Temporal Non-Overlap | For any `logical_provision_id`, no two `IN_FORCE`/`SUPERSEDED` versions may have overlapping closed `[effective_from, effective_until]` windows. | **RELEASE-BLOCKING** |
| **RV-05** | Temporal Gaps & Uncertainty | Gaps between consecutive versions are permitted ONLY if explicitly modeled as `REPEALED`, `SUSPENDED_GAP` (with `gap_or_repeal_reason`), or `EFFECTIVE_DATE_UNRESOLVED` (with `uncertainty_window_start` & `end`). Unexplained gaps fail. | **RELEASE-BLOCKING** (unexplained) / **QUARANTINE** (`UNRESOLVED_HISTORICAL`) |
| **RV-06** | Amendment Lineage | Every version with `effective_from > document.effective_from` must reference an `AmendmentEdge` whose `effective_date == effective_from`. | **QUARANTINE** (`UNRESOLVED_HISTORICAL`) |
| **RV-07** | Document Completeness (Decoupled) | Missing sequential articles set `LegalDocument.completeness = PARTIAL_VERIFIED` and populate `missing_article_numbers`. Does NOT quarantine independently verified sibling provisions. | **REPORTED** in `release_manifest.json` & `validate.py` |
| **RV-08** | Authoritative Arabic (Art. 66) | Every `VERIFIED_PUBLISHABLE` or `PUBLISHED_WITH_CAVEAT` version MUST have an `ar` record with `is_authoritative=True` and `translation_provenance="authoritative_original"`. | **QUARANTINE** (`BLOCKED_LEGAL_TOPIC`) |
| **RV-09** | Bilingual Alignment & Triage Honesty | Structural/numeric parity mismatches block `VERIFIED_PUBLISHABLE` (quarantine or AR-only `PUBLISHED_WITH_CAVEAT`). `unavailable_official` EN is valid with `OFFICIAL_EN_UNAVAILABLE` caveat. Automated heuristics set `numeric_consistency_status="pass"` only—never `semantic_equivalence_status="human_reviewed_equivalent"`. | **QUARANTINE** (`BLOCKED_LEGAL_TOPIC` or AR-only caveat) |
| **RV-10** | Jurisdiction Hygiene | Publishable provisions must have `jurisdiction == Jurisdiction.UAE_ONSHORE`. Zero DIFC/ADGM/domestic-worker provisions permitted. | **RELEASE-BLOCKING** |
| **RV-11** | Fail-Closed Claim-Key Registry | `claim_key_registry.json` SHA-256 matches `release_manifest.json`; every `approved` entry references existing `logical_provision_id` & `applicable_version_ids` and has `reviewer_id` + `reviewed_at_utc`. Unregistered provisions authorize zero claims. | **RELEASE-BLOCKING** (tamper/orphan) / **FAIL-CLOSED AT RUNTIME** |
