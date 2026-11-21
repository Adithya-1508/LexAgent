# UAE-LexAgent

An evaluation-driven, bilingual legal-agent project for UAE employment-law questions.

The system is designed around a simple principle:

> Use probabilistic models for interpretation and language; use structured evidence and deterministic tools for decisions that must be auditable.

## Current milestone

- **Implemented & Merged on `main`**:
  - Step 1: Canonical data models, schemas, and deterministic calculation primitives.
  - Step 2: Official source ingestion, bilingual clause alignments, and Shubhi Signal evaluation dataset.
  - Step 3: Dual-corpus hybrid retrieval engine (BM25 + Dense + RRF + Cross-Encoder reranking).
  - Step 4: Citation-grounded deterministic legal synthesis and verification engine.
  - Step 5: Auditable multi-step reasoning agent with deterministic & LangGraph dual-runtime support.
- **Current Milestone — Phase 5.1 (Correctness & Safety Hardening)**:
  - Implemented on feature branch `fix/phase-5-1-correctness-safety`, currently **awaiting pull request review and acceptance**.
  - **Corpus Scope**: Grounded against current MVP corpus: 13 core employment articles of Federal Decree-Law No. 33 of 2021 (Articles 1, 7, 8, 9, 10, 16, 17, 30, 42, 43, 51, 53, 66) and Cabinet Resolution No. 1 of 2022 (Article 30).
  - **Strict Citation Verification Gate**: Enforces citation existence in corpus, presence in retrieved context, jurisdiction compatibility (UAE onshore only), temporal validity (`effective_from` / `effective_until`), statutory subject alignment (`employer` vs `worker`), statutory polarity limits ("shall not exceed"), and strict numeric consistency.
  - **Statutory Gratuity Hardening**: Validates finite and positive numerical bounds, detects UAE national / Emirati worker pension exclusions (Article 51(1)), part-time work patterns under Cabinet Resolution No. 1 of 2022 Article 30, and deducts unpaid absences (Article 51(2)).
  - **Fail-Closed Finalization Policy**: Prevents partial or contradicted draft release; any gate failure forces `answerable=False`, confidence `0.0`, empty evidence, and an explicit `**Abstention Notice**`.
  - **Audit Trail & Privacy**: Sanitizes internal exception disclosure and redacts sensitive tokens, keys, and passwords from agent trace records.
  - **Multi-Job GitHub Actions CI Pipeline**: 7 dedicated jobs across Python 3.11 and 3.12 (linting, formatting, static type checking, unit tests, legal safety invariants, integrity validation, dependency isolation, wheel packaging, and Bandit security scans).
- **Upcoming Milestones**:
  - Phase 5.2: Authoritative Corpus Reliability (full 74-article FDL 33/2021 ingestion).
  - Phase 6: Production dense multilingual retrieval & Qdrant integration.
  - Phase 9: Supervised Fine-Tuning (LoRA / QLoRA) empirical research pipeline.

## Architecture we are building toward

```text
query -> language/intent -> jurisdiction gate -> temporal retrieval
      -> evidence selection -> tool or answer composition
      -> citation/grounding/schema verification -> answer or abstention
```

Planned components:

- Python + Pydantic: contracts and validation
- FastAPI: API boundary
- LangGraph: explicit agent state and routing
- Qdrant: retrieval infrastructure, never the legal source of truth
- BGE-M3 or a measured alternative: multilingual retrieval
- deterministic Python tools: calculations
- a custom “Shubhi Signal” benchmark: jurisdiction, retrieval, grounding, tools, bilingual consistency, abstention, versioning, and latency

## Setup

Requires Python 3.11+ (CI executes on Python 3.12).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Optional component extras:
- Retrieval: `pip install -e ".[retrieval,qdrant]"`
- Embeddings: `pip install -e ".[embeddings]"`
- Agent & LangGraph: `pip install -e ".[agent]"`
- All optional extras: `pip install -e ".[dev,retrieval,qdrant,embeddings,openai,agent]"`

## Development and CI

### Local Quality Assurance

Before opening a pull request or pushing changes, run the full validation suite:

```powershell
# 1. Lint checks
python -m ruff check .

# 2. Formatting verification (auto-fix with `python -m ruff format .`)
python -m ruff format --check .

# 3. Static type analysis
python -m mypy src/

# 4. Unit and integration tests
python -m pytest -q

# 5. Packaging & distribution check
python -m build --sdist --wheel
```

### GitHub Actions CI Pipeline

The CI workflow (`.github/workflows/ci.yml`) triggers on pushes to `main`, pull requests targeting `main`, and manual dispatches (preventing duplicate workflow executions on feature branches):

1. **Lint, Format & Static Type Analysis (`quality`)**: Validates code with Ruff linter, Ruff format checker, and mypy strict analysis with zero blanket ignores.
2. **Unit & Integration Tests (`unit-tests`)**: Runs the full pytest test suite across Python 3.11 and 3.12, emitting JUnit XML test reports uploaded as workflow artifacts.
3. **Legal Safety & Invariant Tests (`legal-safety`)**: Executes dedicated statutory safety tests (`pytest -m legal_safety`) verifying fail-closed jurisdiction gating, temporal validity, predicate fact checking, and calculation limits.
4. **Corpus & Benchmark Integrity Check (`corpus-benchmark-integrity`)**: Executes the explicit validation CLI (`python -m uae_lexagent.evaluation.validate`), reporting structural integrity separately from corpus completeness without concealing missing provisions, followed by loader unit tests.
5. **Isolated Dependencies (`dependency-isolation`)**: Tests core minimal installation (`.[dev]`), retrieval/Qdrant subset (`.[dev,retrieval,qdrant]`), and agent/LangGraph runtime (`.[dev,retrieval,agent]`) in strict isolation.
6. **Distribution Package & Clean Installation (`packaging`)**: Builds source distribution and binary wheel, provisions a genuinely fresh virtual environment outside the repository (`/tmp/isolated_packaging_venv`), installs the built wheel, and executes model and tool smoke tests from `/tmp` (outside the source checkout) verifying module path isolation.
7. **Static Security Scan (`security-scan`)**: Runs Bandit static security analysis (`bandit -r src/ -ll -q`) to detect security vulnerabilities and unsafe coding patterns.
8. **Dependency Vulnerability Audit (`vulnerability-audit`)**: Scans all installed dependencies for known CVEs using `pip-audit --desc`. Actions are pinned to immutable commit SHAs (`actions/checkout@11bd7190...`, `actions/setup-python@42375524...`, `actions/upload-artifact@4cec3d8a...`).

### Dependency Vulnerability & Exception Policy

- **Vulnerability Threshold**: Any known CVE or security vulnerability detected in project dependencies by `pip-audit` blocks the CI pipeline.
- **Exception Process**: In the rare event that an upstream package has a reported vulnerability without an available fix, an exception may only be granted if:
  1. An issue is filed documenting the exact vulnerability ID, affected component, and impact analysis.
  2. The vulnerable code path is demonstrated to be unreachable or mitigated by application-level controls.
  3. The exception is explicitly scoped in CI using `--ignore-vuln <ID>` with an expiration date not exceeding 30 days.

### Verification Gate Boundaries and Entailment Limitations

The `CitationVerifier` enforces a deterministic gate that prevents ungrounded legal assertions:
- **Scope of Verification**: Verifies official corpus existence, context presence, jurisdiction alignment (UAE onshore), temporal validity (`effective_from` / `effective_until`), statutory subject duty allocation (`employer` vs `worker`), statutory polarity limits ("shall not exceed"), and exact numeric quantity/unit consistency.
- **Entailment Boundaries**: The verifier uses structured claim representations (`VerifiedLegalClaim`) anchored to canonical alignment IDs. Heuristic and structural checks do **NOT** claim full formal mathematical entailment over unconstrained natural language generated by external LLMs. Unverified or drifted claims strictly fail-closed, resulting in an explicit `**Abstention Notice**`.
- **Disclaimer**: UAE-LexAgent is a research and technical evaluation platform; its outputs do not constitute formal legal counsel.

### Official Source Provenance and Corpus Completeness

LexAgent explicitly distinguishes between **structural integrity**, **official-source provenance**, and **authoritative completeness**:
- **Structural Integrity & Provenance**: 100% of ingested provisions, bilingual alignment pairs, and benchmark queries are schema-valid, syntactically verified, and cryptographically checksum-locked to `manifest.json`. The validation CLI (`python -m uae_lexagent.evaluation.validate`) independently inspects all official sources, verifying local file existence and SHA-256 hashes against `data/official_sources/manifest.json`.
- **Upstream Raw Snapshot Status**: Upstream official PDF snapshots are explicitly tracked. Where upstream government endpoints enforce access gates (e.g. HTTP 403 or timeouts preventing raw PDF downloads), this status is reported transparently as `UNRESOLVED_PENDING_PDF_RETRIEVAL` rather than silently concealed or marked as fully resolved.
- **Authoritative Coverage**: The Phase 5.1 corpus is a curated **Partial MVP** containing 13 of 74 articles of Federal Decree-Law No. 33 of 2021 (17.6%) and 1 article of Cabinet Resolution No. 1 of 2022 (Article 30). Missing statutory articles are explicitly tracked and scheduled for Phase 5.2 expansion; unsupported queries outside the ingested corpus strictly fail-closed and abstain without hallucinated claims, as verified by deterministic negative tests in `tests/test_legal_safety.py`.

### Architectural Distinction: Direct Tool API vs Autonomous Agent Gating

LexAgent enforces a strict architectural boundary between direct developer APIs and autonomous legal agent behavior:
- **Direct Calculator API (`calculate_gratuity`)**: Retains standard default arguments (`is_uae_national=False, work_pattern="full_time"`) to provide convenient, deterministic execution for developer scripting, utility tools, and automated testing pipelines where input assumptions are explicitly managed by the caller.
- **Autonomous Legal Agent (`tool_node`)**: Governed by strict legal safety policy. The autonomous agent is **strictly prohibited from making silent factual assumptions**. If a user query provides basic salary and service years but omits worker nationality (UAE national vs expatriate) or work pattern (full-time vs part-time), the agent refuses to default to full-time expatriate status and strictly abstains with `fallback_reason="missing_eligibility_facts"`.
- **Part-Time Gratuity Proration**: Implemented under Cabinet Resolution No. 1 of 2022 Article 30 Clause 1(a)-(b):
  $$\text{Proration \%} = \frac{\text{Contractual Working Hours}}{\text{Full-Time Working Hours}} \times 100$$
  Contractual and full-time hours must share identical temporal units (dimensionless ratio $\le 1.0$). Proration is applied to the full-time statutory entitlement accrued over the worker's service years under Article 51(2)-(3). If contractual hours are omitted, the function returns `total_gratuity=None` with `eligibility_status="non_calculable_missing_hours"` to prevent misleading amounts.

### Contribution Workflow

1. Branch off `main` using standard naming (`fix/<issue-description>` or `feat/<milestone>`).
2. Implement atomic changes with test coverage.
3. Validate locally with all five QA commands above.
4. Submit a pull request against `main`. All CI checks must pass before review and merging.

## Learning path

We will build this in small, inspectable steps:

1. schemas and deterministic tools
2. official-source ingestion into article/clause records
3. local lexical retrieval baseline
4. Qdrant and multilingual retrieval
5. LangGraph routing and abstention
6. FastAPI serving
7. benchmark and ablations
8. fine-tuning only after the baseline is measurable

The important research comparison will be incremental: base model → RAG → tools/verification → fine-tuning → full agent.

