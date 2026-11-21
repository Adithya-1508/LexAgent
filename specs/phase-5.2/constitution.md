# LexAgent Project Constitution (Phase 5.2 Amendments)

*Note: No `constitution.md` currently exists on `main`. This document codifies the existing Phase 5.1 safety guarantees verified in `src/uae_lexagent/models.py`, `src/uae_lexagent/ingestion.py`, `src/uae_lexagent/synthesis/verifier.py`, and `src/uae_lexagent/evaluation/validate.py`, and introduces Phase 5.2 constitutional principles.*

## Article I — Legal Authority & Language Primacy

1. **Arabic Legal Primacy (FDL 33/2021 Art. 66):** Arabic is the sole legally authoritative language for UAE federal legislation. Every publishable provision MUST have an authoritative Arabic text (`is_authoritative=True`, `translation_provenance="authoritative_original"`). English text is never legally authoritative (`is_authoritative=False`).
2. **No Silent Substitution of Unofficial Translations:** Missing official English translations MUST be explicitly marked `translation_provenance="unavailable_official"`. Machine-translated or LLM-generated translations are strictly prohibited inside the canonical statutory corpus.

## Article II — Fail-Closed Safety & Honest Epistemology

3. **Fail-Closed Ordinary Retrieval:** Ordinary production retrieval MUST serve only `VERIFIED_PUBLISHABLE` provisions by default (`include_caveated=False`, `allow_legacy_unverified=False`). Caveated provisions require explicit caller opt-in and mandatory caveat disclosure enforcement.
4. **No Automatic Promotion of Legacy Data:** Pre-Phase 5.2 provisions load as `LEGACY_UNVERIFIED` and MUST NEVER be silently promoted to `VERIFIED_PUBLISHABLE` without completing full snapshot acquisition, authenticity verification, and `RV-01`–`RV-11` validation.
5. **No Automatic Claim Authorization:** Adding a provision to the corpus grants zero authorized legal claims by default. Every claim emitted by synthesis must match an explicitly reviewed, version-scoped entry in `claim_key_registry.json`.
6. **Honest Provenance Separation:** Cryptographic hashing (SHA-256) proves byte-level integrity after capture only. Operator attestation proves documented custody only. Neither may be represented as equivalent to automated TLS-verified or cryptographically signed government-origin authenticity.
7. **Honest Verification Boundaries:** Heuristic citation, numeric, and structural checks are technical safeguards and triage signals—never mathematical proofs of semantic entailment or bilingual legal equivalence.

## Article III — Temporal & Jurisdictional Integrity

8. **Explicit Temporal Grounding:** Every retrieval and citation check MUST bind to a resolved `as_of` date using closed inclusive intervals `[effective_from, effective_until]`. Superseded provisions may never be silently served as current law. Ambiguous temporal overlaps or unresolved effective-date uncertainty windows MUST fail closed.
9. **Strict Jurisdiction Hygiene:** The canonical Phase 5.2 corpus is strictly limited to `uae_onshore` mainland private-sector employment law. DIFC, ADGM, domestic workers (FDL 9/2022), and public-sector HR rules are explicitly out of scope and must trigger deterministic abstention or scope disclosure.

## Article IV — Operational & Solo-Developer Sustainability

10. **Zero Paid Infrastructure:** All ingestion, validation, storage, indexing, evaluation, and CI workflows MUST run locally on commodity hardware and free GitHub Actions tiers without paid cloud services, managed vector databases, or paid LLM APIs.
11. **Core-Minimal Dependency Discipline:** Core runtime dependencies in `pyproject.toml` remain strictly minimal (`pydantic>=2.7,<3`). Corpus acquisition and parsing libraries belong exclusively in an optional `[corpus]` extra so the `core-minimal` CI job stays green.
