# LexAgent Phase 5.2 — Canonical Data Model & Validation Rules (`data-model.md`)

## 1. Core Enums (Zero Ambiguity)

### `AuthenticityTier` (on `LegalSource`)
- `GOV_AUTOMATED_TLS_VERIFIED`: Automated HTTPS capture from allowlisted `*.gov.ae` host with recorded TLS certificate fingerprint, HTTP status, headers, and SHA-256.
- `GOV_INDEPENDENTLY_CORROBORATED`: Manually captured artifact corroborated by an independent government verification mechanism (byte-match against automated mirror, verified UAE government PDF digital signature, or automated portal cross-check).
- `OPERATOR_ATTESTED_ONLY`: Manually supplied file with complete `ManualAttestation`, lacking automated TLS or cryptographic government signature proof. Carries mandatory caveat `OPERATOR_ATTESTED_SOURCE_ONLY`. An operator attestation or caveat is **never** treated as independent government authentication.
- `LEGACY_UNATTESTED`: Default for pre-Phase 5.2 derived `.txt` sources without upstream raw snapshots.

### `DocumentCompleteness` (on `LegalDocument` ONLY)
- `COMPLETE`: 100% of statutory articles extracted and accounted for.
- `PARTIAL_VERIFIED`: Subset of articles extracted and verified; missing article numbers explicitly listed. Independently verified sibling provisions remain eligible for publication.
- `PARTIAL_MVP`: Legacy Phase 5.1 partial coverage state.
- `UNINGESTED`: Registered in Boundary Registry or target list; zero provisions ingested.

### `PublicationState` (on `LegalProvision` ONLY)
- `VERIFIED_PUBLISHABLE`: Passes `RV-01`–`RV-11` with `GOV_AUTOMATED_TLS_VERIFIED` or `GOV_INDEPENDENTLY_CORROBORATED` authenticity and zero active caveats (`caveat_codes == []`). Active caveats (blocking or non-blocking) strictly cannot coexist with `VERIFIED_PUBLISHABLE`. Served by default in ordinary retrieval.
- `PUBLISHED_WITH_CAVEAT`: Authoritative text verified, but carries >=1 mandatory `caveat_codes` (e.g., `STATUTORY_INTERACTION_CR_96_2023`, `OFFICIAL_EN_UNAVAILABLE`, `OPERATOR_ATTESTED_SOURCE_ONLY`). Excluded from ordinary retrieval by default (`include_caveated=False`); requires explicit opt-in and disclosure enforcement.
- `LEGACY_UNVERIFIED`: **Default** for Phase 5.1 records loaded without Phase 5.2 migration. Excluded from ordinary retrieval by default (`allow_legacy_unverified=False`). Never auto-promoted. Legacy opt-ins are confined exclusively to compatibility-test paths.
- `UNRESOLVED_HISTORICAL`: Historical version with unresolved text or Gazette effective date; strictly requires explicit uncertainty boundary bounds (`uncertainty_window_start` and `uncertainty_window_end`) for safe quarantine. Excluded from date-specific retrieval within that window; triggers `temporal_uncertainty` abstention.
- `BLOCKED_LEGAL_TOPIC`: Critical defect (CMap corruption, unreviewed OCR, failed attestation, jurisdiction violation). Hard-excluded from index; triggers `blocked_legal_topic` abstention.

### `LifecycleState` (on `LegalProvision` ONLY)
- `IN_FORCE`, `SUPERSEDED`, `REPEALED`, `ENACTED_PENDING_COMMENCEMENT`, `EFFECTIVE_DATE_UNRESOLVED`, `SUSPENDED_GAP`.

---

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
- **`BoundaryRegistry` Model:**
  - `verified_topics: tuple[str, ...]` — Positively certified statutory topics with complete amendment coverage through `system_cutoff`.
  - `post_cutoff_amendments: dict[str, str]` — Map of topic -> known subsequent statutory instrument enacted after `system_cutoff`.
  - `coverage_status: Literal["COMPLETE", "INCOMPLETE", "UNKNOWN"]` — Global certification status. If `INCOMPLETE` or `UNKNOWN`, all undated queries fail closed (`abstention_reason="incomplete_amendment_verification"`).

---

## 3. Legacy Migration Protocol & Temporal Cutoff Governance

### 3.1 Temporal Cutoff & Current-Law Governance (`effective_as_of_max`)
1. **Four Temporal Dimensions:**
   - `release_manifest.built_at_utc`: Generation timestamp of the corpus release artifact (reproducibly derived from `SOURCE_DATE_EPOCH` / git commit date).
   - `release_manifest.effective_as_of_max`: Upper evaluation limit for the planned corpus (`2024-08-31`). Actual statutory coverage and any unresolved effective-date windows are checked independently for each topic.
   - `LegalDocument.last_verified_at_utc` / `LegalProvision.reviewed_at_utc`: Entity-level audit timestamps.
   - `query.as_of`: Query-specified evaluation date.
2. **Current-Law Query Handling (`query.as_of is None`):**
   - **Positive Topic Requirement (Closed-World):** The engine checks that the target topic is positively listed in `BoundaryRegistry.verified_topics`. The absence of a registered amendment is NEVER proof of complete coverage.
   - **Unknown / Incomplete Registry Guard:** If `BoundaryRegistry.coverage_status` is not `COMPLETE`, or topic is uncertified, abstain (`abstention_reason="incomplete_amendment_verification"`).
   - **Staleness Horizon Guardrail:** If execution date exceeds `system_cutoff` (`2024-08-31`) by > 180 days, the engine MUST fail closed with `abstention_reason="corpus_verification_expired"`. A warning alone **MUST NEVER** authorize a current-law answer. Substantive answers are permitted only for explicit historical queries where `as_of <= system_cutoff`.
   - **Known Post-Cutoff Amendment Guard:** If topic is in `BoundaryRegistry.post_cutoff_amendments`, fail closed (`abstention_reason="stale_or_incomplete_amendment_coverage"`).
   - **Mandatory Disclosure:** If eligible, binds tentatively to `effective_as_of_max` (`2024-08-31`) and attaches `TEMPORAL_CUTOFF_DISCLOSURE`.
3. **Historical Query Handling (`query.as_of is not None`):**
   - If `query.as_of > effective_as_of_max`: fail closed with `abstention_reason="temporal_uncertainty"`.
   - If `query.as_of <= effective_as_of_max`:
     * **Affirmative Historical Coverage Requirement:** The queried topic MUST be certified in `BoundaryRegistry.verified_topics` as having complete verified historical coverage up to `query.as_of`.
     * If historical topic coverage is uncertified, missing, `UNKNOWN`, `INCOMPLETE`, or contradictory, the engine MUST fail closed and abstain (`abstention_reason="unverified_historical_topic_coverage"`). Merely matching an existing historical provision in the corpus is strictly insufficient.
     * Only if affirmatively certified, evaluates exact historical version on `as_of`, attaching `HISTORICAL_ANSWER_CAVEAT` and `TEMPORAL_CUTOFF_DISCLOSURE`.

### 3.2 Canonical Corpus Fingerprint vs. Release Manifest Digest
To prevent semantic conflation between corpus content integrity and manifest file metadata:
1. **Canonical Corpus Fingerprint (`canonical_corpus_fingerprint`):** Defined strictly per `loader.py` and `indexing.py` as the deterministic SHA-256 digest of canonical JSON serialization of all sorted `LegalProvision` records:
```python
records = [provision.model_dump(mode="json") for provision in provisions]
records.sort(key=lambda record: record["provision_id"])
payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
    "utf-8"
)
corpus_fingerprint = hashlib.sha256(payload).hexdigest()
```
2. **Release Manifest Digest (`release_manifest_digest`):** Defined separately as the direct SHA-256 digest of `release_manifest.json` on disk, binding all document artifact checksums, claim-key registry checksums, and publication ledgers.

### 3.3 Synthetic Fixture Integrity & Non-Publication Isolation
Synthetic fixtures in `specs/phase-5.2/fixtures/` carry `SYNTHETIC_TEST_ONLY_...` identifiers and `release_version: "5.2.0-synthetic-test.1"`.
- **Shape-Only Classification:** Fixture hash values and simulated TLS cert fingerprints in test fixtures are classified strictly as shape-only unit-testing data with zero claim of official government origin or production integrity.
- **Strict Publication Isolation:** Synthetic fixtures are prohibited from ever being accepted as release verification evidence or published into production indices. Simulated government source acquisitions are strictly test doubles, never real verified provenance.

---

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
| **RV-08** | Authoritative Arabic (Art. 66) | Every `VERIFIED_PUBLISHABLE` or `PUBLISHED_WITH_CAVEAT` version MUST have an `ar` record with `is_authoritative=True` and `translation_provenance="authoritative_original"`. English can never be authoritative. | **QUARANTINE** (`BLOCKED_LEGAL_TOPIC`) |
| **RV-09** | Bilingual Alignment & Triage Honesty | Structural/numeric parity mismatches block `VERIFIED_PUBLISHABLE`. Arabic and English provisions in an audit pair MUST belong to the exact same `version_id`. `unavailable_official` EN is valid with `OFFICIAL_EN_UNAVAILABLE` caveat. Automated heuristics set `numeric_consistency_status="pass"` only—never `semantic_equivalence_status="human_reviewed_equivalent"`. | **QUARANTINE** (`BLOCKED_LEGAL_TOPIC` or AR-only caveat) |
| **RV-10** | Jurisdiction Hygiene | Publishable provisions must have `jurisdiction == Jurisdiction.UAE_ONSHORE`. Zero DIFC/ADGM/domestic-worker provisions permitted. | **RELEASE-BLOCKING** |
| **RV-11** | Fail-Closed Claim-Key Registry & Clause Binding | `claim_key_registry.json` SHA-256 matches `release_manifest.json`; every `approved` entry references existing `logical_provision_id` & `applicable_version_ids` and has `reviewer_id` + `reviewed_at_utc`. Gratuity threshold `gratuity_entitlement_service_threshold` strictly bound to Article 51(2). Unregistered provisions authorize zero claims. | **RELEASE-BLOCKING** (tamper/orphan) / **FAIL-CLOSED AT RUNTIME** |
