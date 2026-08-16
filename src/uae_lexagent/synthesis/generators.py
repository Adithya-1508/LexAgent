"""Deterministic template generator and optional LLM synthesis providers."""

from __future__ import annotations

import json
import os
import re
import time
from hashlib import sha256
from decimal import Decimal
from typing import Any, Protocol, Sequence

from ..models import Jurisdiction, Language, LegalEvidence, LegalQuery
from .contracts import (
    DraftLegalClaim,
    GeneratedLegalAnswer,
    GeneratorProtocol,
)
from .policy import evaluate_answerability


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


class LLMProvider(Protocol):
    """Protocol for external/local LLM completion providers."""

    def complete(self, prompt: str, *, timeout_seconds: float = 30.0) -> str: ...


class DeterministicTemplateGenerator:
    """Zero-dependency, offline rule-based legal synthesizer using explicit statutory templates."""

    def generate(
        self,
        query: LegalQuery,
        evidence: list[LegalEvidence],
        jurisdiction: Jurisdiction = Jurisdiction.UAE_ONSHORE,
        language: Language = Language.EN,
    ) -> GeneratedLegalAnswer:
        start_time = time.perf_counter()
        answer_id = _stable_id("ans", query.query_id, query.text, language.value)

        # 1. Answerability Policy Check
        decision = evaluate_answerability(query, evidence)
        if not decision.answerable:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return GeneratedLegalAnswer(
                answer_id=answer_id,
                query=query,
                jurisdiction=jurisdiction,
                answerable=False,
                summary=decision.explanation,
                claims=[],
                full_text=f"**Abstention Notice**: {decision.explanation}",
                referenced_evidence=[],
                abstention_reason=decision.reason,
                generation_latency_ms=latency_ms,
            )

        # 2. Build template claims from query & evidence
        claims: list[DraftLegalClaim] = []
        query_cf = query.text.casefold()
        is_arabic = (language == Language.AR)

        # Evidence alignment lookup
        evidence_by_align: dict[str, LegalEvidence] = {}
        for ev in evidence:
            align_id = ev.provision.alignment_id or ev.provision.provision_id
            if align_id not in evidence_by_align:
                evidence_by_align[align_id] = ev

        claim_number = 0

        def claim_id(claim_key: str) -> str:
            nonlocal claim_number
            claim_number += 1
            return _stable_id("claim", query.query_id, claim_key, str(claim_number))

        def has_evidence(*alignment_ids: str) -> bool:
            return all(alignment_id in evidence_by_align for alignment_id in alignment_ids)

        # --- Statutory Template Registry ---

        # Article 9 Clause 1: Probation Max Duration
        if any(w in query_cf for w in ("probation", "تجربة", "اختبار")) and any(
            w in query_cf for w in ("maximum", "duration", "period", "long", "أقصى", "اقصى", "مدة")
        ):
            align_id = "FDL_33_2021_A9_C1"
            if has_evidence(align_id):
                claims.append(
                    DraftLegalClaim(
                        claim_id=claim_id("probation_max_months"),
                        claim_key="probation_max_months",
                        conclusion=(
                            "لا يجوز أن تزيد فترة التجربة على (6) ستة أشهر من تاريخ بدء العمل."
                            if is_arabic
                            else "The probation period shall not exceed 6 months from the date of commencement of work."
                        ),
                        cited_alignment_ids=[align_id],
                        confidence=Decimal("1.0"),
                    )
                )

        # Article 9 Clause 1: Employer Probation Notice (14 days)
        if (
            any(w in query_cf for w in ("probation", "تجربة"))
            and any(w in query_cf for w in ("employer", "صاحب العمل"))
            and any(w in query_cf for w in ("terminate", "notice", "إنهاء", "اخطار", "إخطار", "فصل"))
        ):
            align_id = "FDL_33_2021_A9_C1"
            if has_evidence(align_id):
                claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("probation_employer_notice_days"),
                    claim_key="probation_employer_notice_days",
                    conclusion=(
                        "يجوز لصاحب العمل إنهاء خدمة العامل خلال فترة التجربة بإخطار خطي مدته لا تقل عن (14) يوماً."
                        if is_arabic
                        else "The employer may terminate the worker's service during probation by giving at least 14 days prior written notice."
                    ),
                    cited_alignment_ids=[align_id],
                    confidence=Decimal("1.0"),
                )
                )

        # Article 9 Clause 3: Employee Transfer Notice (30 days) & Recruitment Costs
        if (
            any(w in query_cf for w in ("probation", "تجربة"))
            and any(w in query_cf for w in ("transfer", "another employer", "move", "صاحب عمل آخر", "الانتقال"))
        ):
            align_id = "FDL_33_2021_A9_C3"
            if has_evidence(align_id):
                claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("probation_employee_transfer_notice_months"),
                    claim_key="probation_employee_transfer_notice_months",
                    conclusion=(
                        "يلتزم العامل الذي يرغب بالانتقال لصاحب عمل آخر بإخطار صاحب العمل خطياً بمدة لا تقل عن شهر، مع تعويض تكاليف الاستقدام."
                        if is_arabic
                        else "A worker wishing to transfer to another UAE employer during probation must give at least one month prior written notice, and the new employer must compensate recruitment costs."
                    ),
                    cited_alignment_ids=[align_id],
                    confidence=Decimal("1.0"),
                )
                )

        # Article 9 Clause 2: Successful completion and service credit
        if (
            any(w in query_cf for w in ("probation", "تجربة", "اختبار"))
            and any(w in query_cf for w in ("success", "complete", "continue", "service", "نجاح", "أتم", "استمر", "خدمة"))
            and has_evidence("FDL_33_2021_A9_C2")
        ):
            claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("probation_continuation_service_credit"),
                    claim_key="probation_continuation_service_credit",
                    conclusion=(
                        "تُحسب فترة التجربة ضمن مدة الخدمة عند إتمامها بنجاح واستمرار العامل في العمل."
                        if is_arabic
                        else "When the worker successfully completes probation and continues in employment, the probation period counts as part of the service period."
                    ),
                    cited_alignment_ids=["FDL_33_2021_A9_C2"],
                    confidence=Decimal("1.0"),
                )
            )

        # Article 9 Clause 4: Employee Departure Notice (14 days)
        if (
            any(w in query_cf for w in ("probation", "تجربة"))
            and any(w in query_cf for w in ("leave the country", "leave the state", "resignation", "مغادرة الدولة", "السفر"))
        ):
            align_id = "FDL_33_2021_A9_C4"
            if has_evidence(align_id):
                claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("probation_employee_departure_notice_days"),
                    claim_key="probation_employee_departure_notice_days",
                    conclusion=(
                        "يلتزم العامل الأجنبي الراغب بمغادرة الدولة خلال فترة التجربة بإخطار صاحب العمل خطياً بمدة لا تقل عن (14) يوماً."
                        if is_arabic
                        else "A foreign worker wishing to leave the UAE during probation must provide at least 14 days prior written notice."
                    ),
                    cited_alignment_ids=[align_id],
                    confidence=Decimal("1.0"),
                )
                )

        # Article 10 / Cabinet Res 1 2022 Article 12: Non-Compete Max Duration (2 years)
        if any(w in query_cf for w in ("non-compete", "non compete", "عدم المنافسة", "عدم منافسة")):
            align_id = "FDL_33_2021_A10_C1"
            if has_evidence(align_id):
                claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("non_compete_max_years"),
                    claim_key="non_compete_max_years",
                    conclusion=(
                        "يشترط في شرط عدم المنافسة تحديد المكان والزمان ونوع العمل، على ألا تزيد المدة على سنتين من تاريخ انتهاء العقد."
                        if is_arabic
                        else "A non-compete clause must specify time, geographical scope, and nature of work, with duration not exceeding 2 years."
                    ),
                    cited_alignment_ids=[align_id],
                    confidence=Decimal("1.0"),
                )
                )

        # Article 51: Gratuity Rules
        if any(w in query_cf for w in ("gratuity", "end of service", "severance", "مكافأة نهاية الخدمة", "مكافاة نهاية الخدمة")):
            align_ids = ["FDL_33_2021_A51_C2", "FDL_33_2021_A51_C6"]
            if has_evidence(*align_ids):
                claims.append(
                DraftLegalClaim(
                    claim_id=claim_id("gratuity_calculation"),
                    claim_key="gratuity_calculation",
                    conclusion=(
                        "يستحق العامل مكافأة نهاية الخدمة بواقع (21) يوماً عن كل سنة من السنوات الخمس الأولى، و(30) يوماً عن كل سنة تالية، بحد أقصى سنتين."
                        if is_arabic
                        else "End-of-service gratuity is calculated at 21 days basic salary per year for the first 5 years, and 30 days per year thereafter, capped at 2 years' basic salary."
                    ),
                    cited_alignment_ids=align_ids,
                    confidence=Decimal("1.0"),
                )
                )

        # Do not turn the top lexical hit into an answer.  Unsupported query
        # intents must remain unverified instead of being promoted by retrieval.

        # Construct summary and full text with inline bracketed citations
        summary_lines = []
        for c in claims:
            citations_str = " ".join(f"[{cid}]" for cid in c.cited_alignment_ids)
            summary_lines.append(f"{c.conclusion} {citations_str}".strip())

        full_text = "\n\n".join(summary_lines)
        summary = summary_lines[0] if summary_lines else "No supported template matched the query."

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return GeneratedLegalAnswer(
            answer_id=answer_id,
            query=query,
            jurisdiction=jurisdiction,
            answerable=True,
            summary=summary,
            claims=claims,
            full_text=full_text,
            referenced_evidence=evidence,
            abstention_reason=None,
            generation_latency_ms=latency_ms,
        )


class LLMGenerator:
    """Provider-agnostic LLM synthesis generator with structured JSON output."""

    def __init__(self, provider: LLMProvider, model_name: str = "custom-llm") -> None:
        self.provider = provider
        self.model_name = model_name

    def generate(
        self,
        query: LegalQuery,
        evidence: list[LegalEvidence],
        jurisdiction: Jurisdiction = Jurisdiction.UAE_ONSHORE,
        language: Language = Language.EN,
    ) -> GeneratedLegalAnswer:
        start_time = time.perf_counter()
        answer_id = _stable_id("ans", query.query_id, query.text, language.value, self.model_name)

        # Answerability Gate
        decision = evaluate_answerability(query, evidence)
        if not decision.answerable:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return GeneratedLegalAnswer(
                answer_id=answer_id,
                query=query,
                jurisdiction=jurisdiction,
                answerable=False,
                summary=decision.explanation,
                claims=[],
                full_text=f"**Abstention Notice**: {decision.explanation}",
                referenced_evidence=[],
                abstention_reason=decision.reason,
                generation_latency_ms=latency_ms,
            )

        # Build Context
        evidence_snippets = []
        for ev in evidence:
            align_id = ev.provision.alignment_id or ev.provision.provision_id
            evidence_snippets.append(f"[{align_id}]: {ev.provision.text}")
        context_str = "\n\n".join(evidence_snippets)

        prompt = f"""You are UAE-LexAgent, a rigorous legal assistant for UAE Law.
Answer the following query ONLY using the provided legal evidence.

Query: {query.text}
Language: {language.value}
Jurisdiction: {jurisdiction.value}

Provided Legal Evidence:
{context_str}

Respond in valid JSON format matching this schema:
{{
    "answerable": true,
    "summary": "Concise direct answer",
    "claims": [
        {{
            "claim_key": "semantic_key_e.g_probation_max_months",
            "conclusion": "Statement of law",
            "cited_alignment_ids": ["EXACT_ALIGNMENT_ID_FROM_EVIDENCE"]
        }}
    ]
}}
"""
        try:
            raw_resp = self.provider.complete(prompt, timeout_seconds=30.0)
            # Parse JSON from LLM response (handling possible markdown fences)
            clean_json_str = raw_resp.strip()
            if "```json" in clean_json_str:
                clean_json_str = clean_json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json_str:
                clean_json_str = clean_json_str.split("```")[1].split("```")[0].strip()

            parsed = json.loads(clean_json_str)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("claims", []), list):
                raise ValueError("Provider response must contain a claims list")
            answerable = parsed.get("answerable", True)
            if not isinstance(answerable, bool) or not isinstance(parsed.get("summary", ""), str):
                raise ValueError("Provider response has invalid answerability or summary fields")
            claims = [
                DraftLegalClaim(
                    claim_id=_stable_id("claim", query.query_id, str(c.get("claim_key", "")), str(i)),
                    claim_key=c.get("claim_key", ""),
                    conclusion=c["conclusion"],
                    cited_alignment_ids=c.get("cited_alignment_ids", []),
                    confidence=Decimal("0.9"),
                )
                for i, c in enumerate(parsed.get("claims", []), start=1)
                if isinstance(c, dict) and isinstance(c.get("claim_key"), str)
            ]

            summary_lines = []
            for c in claims:
                citations_str = " ".join(f"[{cid}]" for cid in c.cited_alignment_ids)
                summary_lines.append(f"{c.conclusion} {citations_str}".strip())

            full_text = "\n\n".join(summary_lines)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            return GeneratedLegalAnswer(
                answer_id=answer_id,
                query=query,
                jurisdiction=jurisdiction,
                answerable=answerable,
                summary=parsed.get("summary", ""),
                claims=claims,
                full_text=full_text,
                referenced_evidence=evidence,
                abstention_reason=None,
                generation_latency_ms=latency_ms,
            )
        except Exception:
            # Fallback to abstention on unparseable LLM output
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return GeneratedLegalAnswer(
                answer_id=answer_id,
                query=query,
                jurisdiction=jurisdiction,
                answerable=False,
                summary="Failed to synthesize structured legal answer from provider response.",
                claims=[],
                full_text="**Abstention Notice**: Generation error occurred.",
                referenced_evidence=[],
                abstention_reason="unverified_claim",
                generation_latency_ms=latency_ms,
            )


def get_generator(generator_type: str = "deterministic", **kwargs: Any) -> GeneratorProtocol:
    """Factory helper to obtain configured synthesizer."""
    gen_clean = generator_type.casefold().strip()
    if gen_clean in ("deterministic", "template", "default"):
        return DeterministicTemplateGenerator()
    elif gen_clean in {"openai", "vllm"}:
        try:
            import openai

            class _OpenAIProvider:
                def __init__(self, model: str, base_url: str | None = None) -> None:
                    self.client = openai.OpenAI(
                        api_key=kwargs.get("api_key") or os.getenv("OPENAI_API_KEY") or "local",
                        base_url=base_url,
                    )
                    self.model = model

                def complete(self, prompt: str, *, timeout_seconds: float = 30.0) -> str:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        timeout=timeout_seconds,
                    )
                    return resp.choices[0].message.content or "{}"

            default_model = "gpt-4o" if gen_clean == "openai" else "facebook/opt-125m"
            default_url = None if gen_clean == "openai" else kwargs.get("base_url", "http://localhost:8000/v1")
            return LLMGenerator(
                provider=_OpenAIProvider(kwargs.get("model", default_model), default_url),
                model_name=gen_clean,
            )
        except ImportError as exc:
            raise ImportError(f"openai package required for {gen_clean} generator") from exc
    elif gen_clean == "anthropic":
        try:
            import anthropic

            class _AnthropicProvider:
                def __init__(self, model: str) -> None:
                    self.client = anthropic.Anthropic(api_key=kwargs.get("api_key") or os.getenv("ANTHROPIC_API_KEY"))
                    self.model = model

                def complete(self, prompt: str, *, timeout_seconds: float = 30.0) -> str:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=int(kwargs.get("max_tokens", 1200)),
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text if response.content else "{}"

            return LLMGenerator(
                provider=_AnthropicProvider(kwargs.get("model", "claude-3-5-sonnet-latest")),
                model_name="anthropic",
            )
        except ImportError as exc:
            raise ImportError("anthropic package required for Anthropic generator") from exc
    elif gen_clean == "ollama":
        try:
            import ollama

            class _OllamaProvider:
                def __init__(self, model: str) -> None:
                    self.model = model

                def complete(self, prompt: str, *, timeout_seconds: float = 30.0) -> str:
                    response = ollama.chat(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0},
                    )
                    return response.get("message", {}).get("content", "{}")

            return LLMGenerator(provider=_OllamaProvider(kwargs.get("model", "llama3.1")), model_name="ollama")
        except ImportError as exc:
            raise ImportError("ollama package required for Ollama generator") from exc
    else:
        raise ValueError(
            f"Unknown generator type: '{generator_type}'. Supported types: deterministic, openai, anthropic, ollama, vllm."
        )
