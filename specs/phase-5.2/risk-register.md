# LexAgent Phase 5.2 — Risk Register & Rollback Runbook (`risk-register.md`)

## 1. Risk Register

| Risk ID | Risk Description | Likelihood | Impact | Mitigation & Deterministic Safeguard |
|---|---|---|---|---|
| **R-01** | `uaelegislation.gov.ae` WAF/Cloudflare blocks automated HTTP fetchers (already observed in Phase 5.1 `validate.py`) | High | Medium | Dual-mode acquirer: `ingest-local` with mandatory `ManualAttestation` (`OPERATOR_ATTESTED_ONLY` + caveat unless independently corroborated); CI runs strictly offline |
| **R-02** | UAE portal terms restrict redistributing raw HTML/PDF snapshots in a public Git repository | Medium | High | Explicit `redistribution_clearance` gate in `M5.2.0` (`RV-02`); if redistribution is restricted, commit only SHA-256 manifests + clean extracted text and keep raw binary snapshots in a private/local cache with offline replay |
| **R-03** | Arabic PDF ToUnicode CMap corruption in amending decrees (`1297`, `1760`) | High (Verified) | High | HTML-first extraction (`ADR-002`); automated CMap detector (`RV-03`) quarantines corrupted text as `BLOCKED_LEGAL_TOPIC` until `human_verification_status="extraction_verified"` |
| **R-04** | Original unamended 2021 text of Arts. 8(3), 54, 60 unavailable in official EN | Medium | High | Archive original AR text; explicitly mark missing EN as `translation_provenance="unavailable_official"` with `OFFICIAL_EN_UNAVAILABLE` caveat (never fabricate via LLM) |
| **R-05** | Unresolved Gazette effective date for FDL 14/2022 or FDL 9/2024 | Medium | High | Model explicit `uncertainty_window_start`/`end` (`EFFECTIVE_DATE_UNRESOLVED` / `UNRESOLVED_HISTORICAL`); queries inside window fail closed (`temporal_uncertainty`) |
| **R-06** | Legacy Phase 5.1 fixtures accidentally promoted to `VERIFIED_PUBLISHABLE` | Low (Mitigated) | High | Default `LegalProvision.publication_state = LEGACY_UNVERIFIED` and `RetrievalFilters.allow_legacy_unverified = False`; tested by `test_legacy_defaults.py` |
| **R-07** | New corpus provisions emit unverified claims via permissive claim-key matching | Low (Mitigated) | High | Fail-closed `claim_key_registry.json` (`RV-11`); new provisions authorize zero claims until human-reviewed, version-scoped entries are added |
| **R-08** | New corpus dependencies break `core-minimal` CI job or introduce `pip-audit` CVEs | Medium | Medium | Isolate acquisition/parsing libraries strictly in `[project.optional-dependencies].corpus`; core stays `pydantic`-only |

## 2. Rollback Runbook

Because the corpus is Git-tracked and versioned via `data/official_sources/release_manifest.json`:
1. **Detection:** CI fails `RV-01`–`RV-11`, `legal-safety`, or benchmark regression check; or a post-release defect is discovered in a provision.
2. **Surgical Quarantine (Preferred for Single-Provision Defect):** Set the affected provision's `publication_state = BLOCKED_LEGAL_TOPIC` (or `UNRESOLVED_HISTORICAL`), regenerate `release_manifest.json` (`v5.2.x+1`), and re-run `python -m uae_lexagent.evaluation.validate`.
3. **Full Release Rollback:** `git revert` the release commit (restoring prior `release_manifest.json`, `data/official_sources/*`, and `data/evaluation/manifest.json`), verify `python -m uae_lexagent.evaluation.validate` and `pytest -m legal_safety` pass, and tag the rollback release.
