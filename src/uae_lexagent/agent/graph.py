"""State graph builders supporting both Deterministic and LangGraph runtimes."""

from __future__ import annotations

import time
from typing import Any, Protocol, Sequence

from ..models import LegalProvision, LegalResponse
from ..retrieval import RetrieverProtocol
from ..synthesis.contracts import GeneratorProtocol
from ..synthesis.verifier import CitationVerifier
from .nodes import (
    abstain_node,
    analyze_node,
    finalize_node,
    reformulate_node,
    retrieval_node,
    synthesis_node,
    tool_node,
    verification_node,
)
from .router import (
    route_after_analysis,
    route_after_reformulation,
    route_after_retrieval,
    route_after_synthesis,
    route_after_tool,
    route_after_verification,
)
from .state import AgentState, AgentStatus


class AgentGraphProtocol(Protocol):
    """Common protocol for both agent graph runtimes."""

    def invoke(self, initial_state: AgentState) -> LegalResponse: ...


class DeterministicAgentGraph:
    """Zero-dependency state machine executing the exact graph logic deterministically."""

    def __init__(
        self,
        retriever: RetrieverProtocol,
        generator: GeneratorProtocol,
        verifier: CitationVerifier,
        provisions: Sequence[LegalProvision],
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.verifier = verifier
        self.provisions = list(provisions)

    def invoke(self, state: AgentState) -> LegalResponse:
        """Execute state machine transitions deterministically."""
        while state.has_step_budget() and not state.is_timed_out():
            # 1. Analyze
            state = analyze_node(state, self.provisions)
            action = route_after_analysis(state)

            if action == "abstain":
                state = abstain_node(state, self.verifier)
                return finalize_node(state)

            # 2. Retrieval Loop
            while state.has_step_budget() and not state.is_timed_out():
                state = retrieval_node(state, self.retriever, self.provisions)
                action = route_after_retrieval(state)

                if action == "abstain":
                    state = abstain_node(state, self.verifier)
                    return finalize_node(state)

                if action == "tool":
                    state = tool_node(state)
                    action = route_after_tool(state)
                    if action == "abstain":
                        state = abstain_node(state, self.verifier)
                        return finalize_node(state)

                # 3. Synthesis
                state = synthesis_node(state, self.generator)
                if state.current_status == AgentStatus.ABSTAINED:
                    state = abstain_node(state, self.verifier)
                    return finalize_node(state)

                # 4. Verification
                state = verification_node(state, self.verifier)
                action = route_after_verification(state)

                if action == "finalize":
                    return finalize_node(state)
                elif action == "reformulate":
                    state = reformulate_node(state, self.provisions)
                    action = route_after_reformulation(state)
                    if action == "retrieve":
                        continue  # Loop back to retrieval
                    else:
                        state = abstain_node(state, self.verifier)
                        return finalize_node(state)
                else:  # abstain
                    state = abstain_node(state, self.verifier)
                    return finalize_node(state)

        # Budget or timeout exceeded
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "exhausted_budget"
        state = abstain_node(state, self.verifier)
        return finalize_node(state)


class LangGraphAgentAdapter:
    """Adapter wrapping a compiled LangGraph StateGraph to match the AgentGraphProtocol."""

    def __init__(self, compiled_graph: Any) -> None:
        self.graph = compiled_graph

    def invoke(self, initial_state: AgentState) -> LegalResponse:
        """Invoke LangGraph and extract final canonical LegalResponse."""
        state_dict = initial_state.model_dump()
        result_dict = self.graph.invoke(
            state_dict,
            config={"recursion_limit": max(25, initial_state.max_steps + 5)},
        )

        # Reconstruct AgentState from resulting state dictionary
        res_state = AgentState.model_validate(result_dict)
        return finalize_node(res_state)


def build_langgraph_agent(
    retriever: RetrieverProtocol,
    generator: GeneratorProtocol,
    verifier: CitationVerifier,
    provisions: Sequence[LegalProvision],
) -> AgentGraphProtocol:
    """Build and compile native LangGraph StateGraph with identical node logic."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ImportError("langgraph and langchain-core are required to use build_langgraph_agent()") from exc

    prov_list = list(provisions)

    # Wrap node functions to operate on state dictionary
    def _analyze(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return analyze_node(state_obj, prov_list).model_dump()

    def _retrieve(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return retrieval_node(state_obj, retriever, prov_list).model_dump()

    def _tool(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return tool_node(state_obj).model_dump()

    def _synthesize(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return synthesis_node(state_obj, generator).model_dump()

    def _verify(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return verification_node(state_obj, verifier).model_dump()

    def _reformulate(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return reformulate_node(state_obj, prov_list).model_dump()

    def _abstain(s: dict[str, Any]) -> dict[str, Any]:
        state_obj = AgentState.model_validate(s)
        return abstain_node(state_obj, verifier).model_dump()

    builder = StateGraph(dict)

    builder.add_node("analyze", _analyze)
    builder.add_node("retrieve", _retrieve)
    builder.add_node("tool", _tool)
    builder.add_node("synthesize", _synthesize)
    builder.add_node("verify", _verify)
    builder.add_node("reformulate", _reformulate)
    builder.add_node("abstain", _abstain)

    builder.set_entry_point("analyze")

    builder.add_conditional_edges(
        "analyze",
        lambda s: route_after_analysis(AgentState.model_validate(s)),
        {"abstain": "abstain", "retrieve": "retrieve"},
    )

    builder.add_conditional_edges(
        "retrieve",
        lambda s: route_after_retrieval(AgentState.model_validate(s)),
        {"tool": "tool", "synthesize": "synthesize", "abstain": "abstain"},
    )

    builder.add_conditional_edges(
        "tool",
        lambda s: route_after_tool(AgentState.model_validate(s)),
        {"synthesize": "synthesize", "abstain": "abstain"},
    )

    builder.add_conditional_edges(
        "synthesize",
        lambda s: route_after_synthesis(AgentState.model_validate(s)),
        {"verify": "verify", "abstain": "abstain"},
    )

    builder.add_conditional_edges(
        "verify",
        lambda s: route_after_verification(AgentState.model_validate(s)),
        {"finalize": END, "reformulate": "reformulate", "abstain": "abstain"},
    )

    builder.add_conditional_edges(
        "reformulate",
        lambda s: route_after_reformulation(AgentState.model_validate(s)),
        {"retrieve": "retrieve", "abstain": "abstain"},
    )

    builder.add_edge("abstain", END)

    compiled = builder.compile()
    return LangGraphAgentAdapter(compiled)
