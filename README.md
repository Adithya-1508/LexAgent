# UAE-LexAgent

An evaluation-driven, bilingual legal-agent project for UAE employment-law questions.

The system is designed around a simple principle:

> Use probabilistic models for interpretation and language; use structured evidence and deterministic tools for decisions that must be auditable.

## Current milestone

Milestone 1 establishes the canonical data model and a deterministic gratuity-calculation primitive. It intentionally does not claim that the included default policy is a statement of current UAE law. Legal rules will be added only through versioned, official-source corpus records and tested fixtures.

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

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

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

