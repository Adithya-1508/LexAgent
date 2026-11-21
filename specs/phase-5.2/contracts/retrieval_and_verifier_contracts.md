# Retrieval, Multi-Version Indexing & Verification Contracts

*Repo-grounded against `src/uae_lexagent/retrieval/contracts.py`, `src/uae_lexagent/retrieval/indexing.py`, `src/uae_lexagent/synthesis/contracts.py`, and `src/uae_lexagent/synthesis/verifier.py`.*

## 1. Fail-Closed `RetrievalFilters` Contract (`retrieval/contracts.py`)

```python
class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    as_of: date | None = None
    language: Language = Language.UNKNOWN
    include_translations: bool = True
    authoritative_only: bool = False
    document_ids: tuple[str, ...] = ()
    legal_domain: str | None = None

    # Phase 5.2 fail-closed eligibility controls
    include_caveated: bool = False
    accepted_caveat_codes: tuple[str, ...] = ()
    allow_legacy_unverified: bool = False
```

### Eligibility Filter Rules (`matches_retrieval_filters`)
1. `BLOCKED_LEGAL_TOPIC` and `UNRESOLVED_HISTORICAL` ALWAYS return `False`.
2. `LEGACY_UNVERIFIED` returns `False` unless `filters.allow_legacy_unverified is True`.
3. `PUBLISHED_WITH_CAVEAT` returns `False` unless `filters.include_caveated is True` **AND** (either `filters.accepted_caveat_codes` is empty with downstream disclosure enforcement enabled, OR `set(provision.caveat_codes).issubset(set(filters.accepted_caveat_codes))`).
4. `VERIFIED_PUBLISHABLE` is eligible subject to jurisdiction, language, document, domain, and `as_of` filters.

## 2. Multi-Version `RetrievalIndex` Contract (`retrieval/indexing.py`)

### Index Maps
- `provisions_by_id: dict[str, LegalProvision]` — unique `provision_id`.
- `version_map: dict[str, dict[str, LegalProvision]]` — keyed by `version_id -> {lang: LegalProvision}`. Duplicate `(version_id, language)` raises `ValueError`.
- `versions_by_logical_id: dict[str, list[str]]` — maps `logical_provision_id -> list[version_id]` sorted by `effective_from`.
- `alignment_map: dict[str, dict[str, LegalProvision]]` — backward-compatible map populated with the single active (`IN_FORCE`) version per `alignment_id` (or the sole version in single-version legacy corpora).
- `quarantine_registry: dict[str, list[LegalProvision]]` — non-indexed metadata store for `UNRESOLVED_HISTORICAL` and `BLOCKED_LEGAL_TOPIC` provisions (never inserted into `vector_store` or `sparse_index`), used strictly to emit accurate abstention reasons.

### Build Eligibility
- `build_retrieval_index(..., include_legacy_unverified: bool = False)`:
  - Indexes `VERIFIED_PUBLISHABLE` and `PUBLISHED_WITH_CAVEAT` (filtered at query time by `include_caveated`).
  - Indexes `LEGACY_UNVERIFIED` ONLY when `include_legacy_unverified=True` (for Phase 5.1 legacy test fixtures).
  - Routes `UNRESOLVED_HISTORICAL` and `BLOCKED_LEGAL_TOPIC` exclusively to `quarantine_registry`.

## 3. Exact `as_of` Resolution Semantics

Let `resolved_as_of = filters.as_of or query.as_of or index.effective_as_of_max`.

For any candidate `logical_provision_id`:
1. **Uncertainty Window Check (First):** If `quarantine_registry` contains an `UNRESOLVED_HISTORICAL` version for `logical_provision_id` with `uncertainty_window_start <= resolved_as_of <= uncertainty_window_end` -> **fail closed** with `abstention_reason="temporal_uncertainty"`.
2. **Blocked Topic Check:** If the matching version for `resolved_as_of` is in `quarantine_registry` with `BLOCKED_LEGAL_TOPIC` -> **fail closed** with `abstention_reason="blocked_legal_topic"`.
3. **Closed Inclusive Interval Selection:** Select all eligible versions where:
   - `(effective_from is None or effective_from <= resolved_as_of)` AND
   - `(effective_until is None or resolved_as_of <= effective_until)` AND
   - `lifecycle_state in {IN_FORCE, SUPERSEDED}` (excluding `ENACTED_PENDING_COMMENCEMENT`, `REPEALED`, `SUSPENDED_GAP`).
4. **Cardinality Enforcement:**
   - `count == 0`: Not in force on `resolved_as_of` -> excluded (abstain `not_in_force_on_date` if no evidence remains).
   - `count == 1`: Selected deterministically. Attach `resolved_as_of` to retrieval metadata.
   - `count > 1`: Overlapping active versions (defense-in-depth beyond `RV-04`) -> **fail closed** with `abstention_reason="ambiguous_temporal_overlap"`. Never arbitrarily pick one.
5. **Missing `as_of` (`None`):** Always binds to `index.effective_as_of_max` (from `release_manifest.json`) and records `resolved_as_of` on `GroundedLegalAnswer`. Superseded historical versions are never returned when `as_of is None`.

## 4. Fail-Closed `CitationVerifier` & Claim-Key Registry Contract (`synthesis/verifier.py`)

### Data-Driven Claim-Key Registry (`data/official_sources/claim_key_registry.json`)
- Replaces hardcoded `_ALIGNMENT_CLAIM_KEYS`.
- Loaded at `CitationVerifier` initialization with SHA-256 verification against `release_manifest.json` (in strict/release mode).
- Only entries with `review_status == "approved"` are active.
- **Zero Auto-Authorization:** Any `logical_provision_id` / `version_id` without an `approved` registry entry yields an empty allowed `claim_key` set -> every claim citing it fails closed as `VerificationStatus.UNVERIFIED` (`no_authorized_claim_keys`).
- **Version Scoping:** An entry applies ONLY if the retrieved evidence's `version_id` is in `entry.applicable_version_ids`. When an amendment creates a new `version_id` (e.g., Art. 54 `@2024-08-31`), prior claim keys do NOT carry over automatically.

### Citation Resolution & Caveat Disclosure Enforcement
1. `CitationVerifier` accepts citations by `version_id` or legacy `alignment_id`. If a legacy `alignment_id` maps to >1 distinct `version_id` in `evidence`, fail closed (`CONTRADICTED`, `ambiguous_version_citation`).
2. If any referenced evidence has `publication_state == PUBLISHED_WITH_CAVEAT`:
   - Collect all unique `caveat_codes` from referenced evidence.
   - Verify every caveat code has a corresponding mandatory disclosure in `GroundedLegalAnswer.caveat_disclosures` (and rendered notice in `full_text`).
   - If any caveat is undisclosed -> fail closed (`ABSTAINED`, `undisclosed_caveat`).
