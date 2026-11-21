"""Conditional routing functions for agent state transitions."""

from __future__ import annotations

from typing import Literal

from .state import AgentIntent, AgentState, AgentStatus


def route_after_analysis(state: AgentState) -> Literal["abstain", "retrieve"]:
    """Determine whether to proceed with retrieval or immediately abstain."""
    if state.intent == AgentIntent.ABSTENTION or state.current_status == AgentStatus.ABSTAINED:
        return "abstain"
    return "retrieve"


def route_after_retrieval(state: AgentState) -> Literal["tool", "synthesize", "abstain"]:
    """Route to statutory tool execution if calculation intent present, otherwise directly to synthesis."""
    if state.current_status == AgentStatus.ABSTAINED:
        return "abstain"
    if state.intent == AgentIntent.CALCULATION:
        return "tool"
    return "synthesize"


def route_after_tool(state: AgentState) -> Literal["synthesize", "abstain"]:
    """Route from tool execution to synthesis."""
    if state.current_status == AgentStatus.ABSTAINED:
        return "abstain"
    return "synthesize"


def route_after_synthesis(state: AgentState) -> Literal["verify", "abstain"]:
    """Route to verification, or directly to abstention if synthesis failed/budget exhausted."""
    if state.current_status == AgentStatus.ABSTAINED:
        return "abstain"
    return "verify"


def route_after_verification(state: AgentState) -> Literal["finalize", "reformulate", "abstain"]:
    """Route based on verification gate outcome and remaining retry budget."""
    if state.current_status == AgentStatus.VERIFIED:
        return "finalize"
    if state.current_status == AgentStatus.RETRYING:
        return "reformulate"
    return "abstain"


def route_after_reformulation(state: AgentState) -> Literal["retrieve", "abstain"]:
    """Route from reformulation back to retrieval loop or to abstention if unrecoverable."""
    if state.current_status == AgentStatus.RETRIEVING:
        return "retrieve"
    return "abstain"
