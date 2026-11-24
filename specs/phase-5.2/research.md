# LexAgent Phase 5.2 — Legal & Technical Research (`research.md`)

## 1. Repository Grounding Findings (Verified from `main`)

Direct inspection of `pyproject.toml`, `data/official_sources/*`, `src/uae_lexagent/models.py`, `ingestion.py`, `retrieval/contracts.py`, `retrieval/indexing.py`, `synthesis/contracts.py`, `synthesis/verifier.py`, `evaluation/validate.py`, and `.github/workflows/ci.yml` established:

1. **Current Coverage:** 13 of 74 articles of FDL 33/2021 and 1 article (Article 30, Clause 1) of Cabinet Resolution No. 1 of 2022 (`partial_mvp`). All 4 raw files in `data/official_sources/raw/` are 198–820 byte derived `.txt` files referencing unretrieved upstream `*_PDF` IDs.
2. **Existing Date Discrepancy:** `data/official_sources/fdl_33_2021_document.json` records `issued_date: "2021-11-15"` and `official_gazette_date: "2021-11-30"`. Primary verification on `uaelegislation.gov.ae/en/legislations/1541` shows Issued `2021-09-20` and Official Gazette No. 712 (Supplement) dated `2021-09-26` (15 Nov 2021 was the public announcement date). Must be corrected in M5.2.0.
3. **Closed Inclusive Dates:** `models.py`, `retrieval/contracts.py`, and `synthesis/verifier.py` all implement closed inclusive `[effective_from, effective_until]` checks (`effective_from <= as_of <= effective_until`).
4. **Multi-Version Collision:** `RetrievalIndex.__init__()` raises `ValueError` on duplicate `(alignment_id, language)`, and `CitationVerifier.verify()` keys `evidence_by_align` by `alignment_id`. Both require version-scoped indexing.
5. **Hardcoded Claim Ontology:** `CitationVerifier` hardcodes 14 `alignment_id` entries in `_ALIGNMENT_CLAIM_KEYS`.
6. **Validation Split:** `ingestion.validate_source_manifest()` fails on missing `derived_from_source_id`, whereas `evaluation/validate.py` tolerates it as `unresolved_pending_pdf_retrieval` and hardcodes only 2 instrument file pairs.
7. **Strict CI Isolation:** `core-minimal` job installs `.[dev]` (only `pydantic>=2.7,<3` at runtime) and runs `test_models.py`, `test_ingestion.py`, `test_tools.py`, `test_routing.py`. Security jobs run `bandit -r src/ -ll -q` and `pip-audit`.

## 2. Verified Primary UAE Legal-Source Inventory

Secondary legal/HR blogs were audited and **explicitly rejected** as sources due to verified errors (e.g., misstating FDL 14/2022's scope or claiming a 3-year contract cap still applies). Only official UAE government portals (`uaelegislation.gov.ae`, `mohre.gov.ae`) are permitted.

| Instrument ID | Official Title | Primary Official URL(s) | Verified Primary Facts | Extraction Constraints & Open Issues |
|---|---|---|---|---|
| `FDL_33_2021` | Federal Decree-Law No. (33) of 2021 Regarding the Regulation of Employment Relationships | EN: `https://uaelegislation.gov.ae/en/legislations/1541`<br>AR: `https://uaelegislation.gov.ae/ar/legislations/1541` | Issued: `2021-09-20` (13 Safar 1443 AH); Gazette No. `712` Supplement (`2021-09-26`); Effective: `2022-02-02` (Art. 74); State: Active; 74 articles (Articles 1–74) | Browser DOM verified. Automated CLI download blocked by Cloudflare WAF (HTTP 403); upstream PDF unretrieved. Original 2021 pre-amendment text of Arts. 8(3), 54, 60 pending isolation from Gazette 712 in M5.2.2. |
| `CABINET_RES_1_2022` | Cabinet Resolution No. (1) of 2022 Concerning the Executive Regulation of FDL 33/2021 | EN: `https://uaelegislation.gov.ae/en/legislations/1547`<br>AR: `https://uaelegislation.gov.ae/ar/legislations/1547` | Enacted/Issued: `2022-02-03` (2 Rajab 1443 AH; Cabinet session approval `2022-01-17`); Gazette No. `721` (`2022-02-15`); Effective: `2022-02-02` (Art. 39); State: Active; Exactly 39 articles (Articles 1–39 verified, concluding with Art. 36 Emergency, Art. 37 Ministerial Dec, Art. 38 Repeal, Art. 39 In Force) | Browser DOM verified. Automated CLI download blocked by Cloudflare WAF (HTTP 403); upstream PDF unretrieved. Full clause-level text extraction to be performed in M5.2.2. |
| `FDL_14_2022` | Federal Decree-Law No. (14) of 2022 Amending Certain Provisions of FDL 33/2021 | EN: `https://uaelegislation.gov.ae/en/legislations/1637`<br>AR: `https://uaelegislation.gov.ae/ar/legislations/1637` | Issued: `2022-09-15` (19 Safar 1444 AH); Gazette No. `736` (`2022-09-28`); Effective: `2022-09-29` (day following publication per Art. 3); Art. 1 replaces Art. 8(3) removing 3-year fixed-term cap | Fully resolved in M5.2.0 (Gazette 736, eff. 2022-09-29). Text verified from HTML portal pages. |
| `FDL_20_2023` | Federal Decree-Law No. (20) of 2023 | [Official Arabic text](https://uaelegislation.gov.ae/ar/constitution/modifications/1297/download) | Issued `2023-09-13` (27 Safar 1445 AH). Effective `2024-01-01` per Article 2. Article 54(3) challenges to Article 54(2) decisions went to the Court of Appeal within 15 working days. | Exact Gazette publication metadata remain unverified. See EVID-12. |
| `FDL_9_2024` | Federal Decree-Law No. (9) of 2024 | [Official Arabic text](https://uaelegislation.gov.ae/ar/constitution/modifications/1760/download) | Issued `2024-07-29` (23 Muharram 1446 AH). Articles 1 and 2 establish direct filing under Article 54(3) and the limited pending-case transfer. Article 4 specifies commencement after 30 days from publication. | Exact commencement remains unresolved. See [EVID-17](M5.2.0_Legal_Source_Verification.md#32-federal-decree-law-no-9-of-2024-procedural-shift-transition--penalties) for the publication evidence and abstention window. |

## 3. Boundary Registry (Related / Out-of-Scope Instruments)

| Instrument | Primary URL | Phase 5.2 Handling |
|---|---|---|
| Cabinet Resolution No. 96 of 2023 (Voluntary EOSB Savings Scheme) | `https://uaelegislation.gov.ae/en/legislations/2309` | Deferred to Phase 5.3; mandatory applicability caveat (`STATUTORY_INTERACTION_CR_96_2023`) on Art. 51 |
| Cabinet Resolution No. 18 of 2022 (Establishment Classification) | `https://uaelegislation.gov.ae/en/legislations/1558` | Out of scope (Phase 5.3+); tracked in Boundary Registry |
| Cabinet Resolution No. 33 of 2022 (Work Injuries Tables) | `https://uaelegislation.gov.ae/en/legislations/1559/download` | Out of scope (Phase 5.3+); tracked in Boundary Registry |
| Ministerial Resolution No. 340 of 2026 (WPS Rules, eff. 01 Jun 2026) | MoHRE Portal / Official Gazette (boundary registry) | Out of scope (Phase 5.3+); prevents stale WPS assertions |
| Federal Decree-Law No. 9 of 2022 (Domestic Workers) | `https://uaelegislation.gov.ae/en/legislations/1593/download` | Strictly excluded (non-mainland-private-sector regime) |
| DIFC Employment Law / ADGM Employment Regulations | N/A | Strictly excluded (`Jurisdiction.DIFC` / `Jurisdiction.ADGM`) |

## 4. Technical Finding: Arabic PDF CMap Corruption

Official UAE Gazette/modification PDFs (`1297/download`, `1760/download`) embed Arabic fonts with incomplete ToUnicode CMap tables, causing deterministic text extractors (`pdftotext`, `PyMuPDF`) to emit presentation-form glitches, split ligatures, and replacement characters (`\ufffd`). In contrast, `uaelegislation.gov.ae` HTML pages serve clean logical-order UTF-8 Arabic. Therefore, Phase 5.2 adopts **HTML-first deterministic extraction** (`ADR-002`) and uses automated CMap corruption detection + mandatory human review (`RV-03`) for any PDF-only amending decree.
