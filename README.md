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
4. **Corpus & Benchmark Integrity Check (`corpus-benchmark-integrity`)**: Validates corpus and benchmark JSON/JSONL manifests, cryptographic checksums, and bilingual symmetry.
5. **Isolated Dependencies (`dependency-isolation`)**: Tests core minimal installation (`.[dev]`), retrieval/Qdrant subset (`.[dev,retrieval,qdrant]`), and agent/LangGraph runtime (`.[dev,retrieval,agent]`) in strict isolation.
6. **Distribution Package & Clean Installation (`packaging`)**: Builds source distribution and binary wheel, confirming installation in a clean environment and running an import smoke test.
7. **Security Vulnerability Scan (`security-scan`)**: Runs Bandit static security analysis (`bandit -r src/ -ll -q`) to detect security vulnerabilities and unsafe coding patterns.

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

