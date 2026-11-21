"""State definitions and trace contracts for the agentic reasoning engine."""

from __future__ import annotations

import re
import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    AgentTraceStep,
    Jurisdiction,
    Language,
    LegalEvidence,
    LegalQuery,
    ToolCall,
)
from ..synthesis.contracts import GeneratedLegalAnswer, GroundedLegalAnswer

_SENSITIVE_DATA_PATTERN = re.compile(
    r"(?:bearer\s+[a-zA-Z0-9_\-\.]+|[a-zA-Z0-9_\-\.]{25,}|password[:=]\s*\S+|token[:=]\s*\S+)",
    re.IGNORECASE,
)


def _sanitize_trace_text(text: str) -> str:
    """Mask credentials, tokens, or raw secrets from trace text."""
    if not text:
        return ""
    return _SENSITIVE_DATA_PATTERN.sub("[REDACTED]", text)


class AgentIntent(str, Enum):
    """Categorized intent of the incoming legal query."""

    DIRECT_FACTUAL = "direct_factual"
    MULTI_HOP = "multi_hop"
    CALCULATION = "calculation"
    ABSTENTION = "abstention"
    CLARIFICATION = "clarification"


class AgentStatus(str, Enum):
    """Execution status of the agentic workflow."""

    RUNNING = "running"
    RETRIEVING = "retrieving"
    SYNTHESIZING = "synthesizing"
    VERIFYING = "verifying"
    RETRYING = "retrying"
    VERIFIED = "verified"
    ABSTAINED = "abstained"
    FAILED = "failed"


class AgentState(BaseModel):
    """Unified state schema driving both deterministic and LangGraph runtimes."""

    model_config = ConfigDict(extra="forbid")

    # Queries and Routing
    query: LegalQuery
    routed_query: LegalQuery | None = None
    language: Language = Language.UNKNOWN
    output_language: Literal["en", "ar"] = "en"
    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    intent: AgentIntent = AgentIntent.DIRECT_FACTUAL

    # Multi-hop & Decomposition
    sub_queries: list[str] = Field(default_factory=list)
    completed_sub_queries: list[str] = Field(default_factory=list)

    # Retrieval & Evidence
    retrieved_evidence: list[LegalEvidence] = Field(default_factory=list)
    deduplicated_alignment_ids: set[str] = Field(default_factory=set)

    # Statutory Tool Execution
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: dict[str, Any] = Field(default_factory=dict)

    # Synthesis & Verification
    draft_answer: GeneratedLegalAnswer | None = None
    verified_answer: GroundedLegalAnswer | None = None

    # Retry, Budgets & Limits
    step_count: int = 0
    execution_steps: int = 0
    verification_attempts: int = 0
    max_attempts: int = Field(default=2, ge=1, le=10)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_execution_steps: int = Field(default=20, ge=1, le=100)
    max_trace_steps: int = Field(default=50, ge=10, le=200)
    max_sub_queries: int = Field(default=4, ge=1, le=20)
    max_evidence_items: int = Field(default=10, ge=1, le=100)
    timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    start_time: float = Field(default_factory=time.perf_counter)
    visited_reformulation_keys: set[str] = Field(default_factory=set)

    # Terminal Status & Provable Audit Trail
    current_status: AgentStatus = AgentStatus.RUNNING
    abstention_reason: str | None = None
    trace: list[AgentTraceStep] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def add_trace(
        self,
        node_name: str,
        action: str,
        status: str,
        rationale: str,
        duration_ms: float,
        input_summary: str = "",
        output_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append an immutable, privacy-safe audit record to trace."""
        is_terminal = node_name in ("abstain_node", "finalize_node")
        # Reserve 2 terminal slots so abstain_node and finalize_node are always auditable
        capacity = self.max_trace_steps if is_terminal else max(0, self.max_trace_steps - 2)
        if len(self.trace) >= capacity:
            return

        sanitized_input = _sanitize_trace_text(input_summary)[:200]
        sanitized_output = _sanitize_trace_text(output_summary)[:200]
        sanitized_rationale = _sanitize_trace_text(rationale)

        sanitized_meta = {}
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, str):
                    sanitized_meta[k] = _sanitize_trace_text(v)
                else:
                    sanitized_meta[k] = v

        step = AgentTraceStep(
            step_index=len(self.trace),
            node_name=node_name,
            action=action,
            status=status,
            rationale=sanitized_rationale,
            duration_ms=round(duration_ms, 3),
            input_summary=sanitized_input,
            output_summary=sanitized_output,
            metadata=sanitized_meta,
        )
        self.trace.append(step)

    def is_timed_out(self) -> bool:
        """Check if execution has exceeded wall-clock timeout budget."""
        return (time.perf_counter() - self.start_time) >= self.timeout_seconds

    def has_step_budget(self) -> bool:
        """Check if agent has remaining step budget for state machine execution."""
        return self.step_count < self.max_steps and self.execution_steps < self.max_execution_steps
