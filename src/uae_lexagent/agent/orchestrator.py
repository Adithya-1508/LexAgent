"""High-level legal agent orchestrator entrypoint."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, Sequence

from ..models import (
    Jurisdiction,
    Language,
    LegalProvision,
    LegalQuery,
    LegalResponse,
)
from ..retrieval import RetrieverProtocol
from ..synthesis.contracts import GeneratorProtocol
from ..synthesis.generators import DeterministicTemplateGenerator
from ..synthesis.verifier import CitationVerifier
from .graph import (
    AgentGraphProtocol,
    DeterministicAgentGraph,
    build_langgraph_agent,
)
from .state import AgentState


class LegalAgentOrchestrator:
    """Stateful, auditable legal reasoning agent orchestrator."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        provisions: Sequence[LegalProvision],
        generator: GeneratorProtocol | None = None,
        verifier: CitationVerifier | None = None,
        runtime: Literal["deterministic", "langgraph"] = "deterministic",
        max_attempts: int = 2,
        max_steps: int = 20,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.retriever = retriever
        self.provisions = list(provisions)
        self.generator = generator or DeterministicTemplateGenerator()

        valid_alignments = {p.alignment_id for p in self.provisions if p.alignment_id}
        self.verifier = verifier or CitationVerifier(valid_corpus_alignment_ids=valid_alignments, strict=True)

        self.runtime = runtime
        if self.runtime not in {"deterministic", "langgraph"}:
            raise ValueError("runtime must be 'deterministic' or 'langgraph'")
        self.max_attempts = max_attempts
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

        # Instantiate graph runtime
        if runtime == "langgraph":
            self.graph: AgentGraphProtocol = build_langgraph_agent(
                retriever=self.retriever,
                generator=self.generator,
                verifier=self.verifier,
                provisions=self.provisions,
            )
        else:
            self.graph = DeterministicAgentGraph(
                retriever=self.retriever,
                generator=self.generator,
                verifier=self.verifier,
                provisions=self.provisions,
            )

    def run(self, query: LegalQuery | str) -> LegalResponse:
        """Execute end-to-end multi-step legal reasoning workflow."""
        if isinstance(query, str):
            normalized_text = " ".join(query.split())
            stable_id = sha256(normalized_text.casefold().encode("utf-8")).hexdigest()[:16]
            q = LegalQuery(
                query_id=f"q_{stable_id}",
                text=normalized_text,
                language=Language.UNKNOWN,
                jurisdiction=Jurisdiction.UNKNOWN,
            )
        else:
            q = query

        initial_state = AgentState(
            query=q,
            max_attempts=self.max_attempts,
            max_steps=self.max_steps,
            timeout_seconds=self.timeout_seconds,
        )

        return self.graph.invoke(initial_state)
