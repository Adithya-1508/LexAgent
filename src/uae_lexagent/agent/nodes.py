"""Stateless graph node functions for the legal agent orchestrator."""

from __future__ import annotations

import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Literal, Sequence

from ..models import (
    Claim,
    Jurisdiction,
    Language,
    LegalEvidence,
    LegalProvision,
    LegalQuery,
    LegalResponse,
    ToolCall,
    VerificationResult,
)
from ..retrieval import RetrievalFilters, RetrieverProtocol, matches_retrieval_filters
from ..routing import classify_jurisdiction
from ..synthesis.contracts import (
    GeneratorProtocol,
    GroundedLegalAnswer,
    VerificationStatus,
)
from ..synthesis.policy import evaluate_answerability
from ..synthesis.verifier import CitationVerifier
from ..tools import GratuityInput, calculate_gratuity
from .state import AgentIntent, AgentState, AgentStatus


def _detect_language(text: str) -> tuple[Language, Literal["en", "ar"]]:
    """Detect Language enum and deterministic output language ('en' or 'ar')."""
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    has_english = bool(re.search(r"[a-zA-Z]", text))

    if has_arabic and has_english:
        arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
        english_chars = len(re.findall(r"[a-zA-Z]", text))
        output_lang: Literal["en", "ar"] = "ar" if arabic_chars >= english_chars else "en"
        return Language.MIXED, output_lang
    elif has_arabic:
        return Language.AR, "ar"
    elif has_english:
        return Language.EN, "en"
    return Language.UNKNOWN, "en"


def analyze_node(
    state: AgentState, provisions: Sequence[LegalProvision] | None = None
) -> AgentState:
    """Analyze query text, classify jurisdiction, check answerability, and decompose multi-hop intents."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    # 1. Language Detection
    lang, out_lang = _detect_language(state.query.text)
    state.language = lang if state.query.language == Language.UNKNOWN else state.query.language
    if state.query.language in (Language.EN, Language.AR):
        state.output_language = state.query.language.value
    else:
        state.output_language = out_lang

    # 2. Jurisdiction Routing
    juris_decision = classify_jurisdiction(state.query.text)
    routed_juris = (
        state.query.jurisdiction
        if state.query.jurisdiction != Jurisdiction.UNKNOWN
        else juris_decision.jurisdiction
    )
    state.jurisdiction = routed_juris

    state.routed_query = state.query.model_copy(
        update={
            "language": state.language,
            "jurisdiction": state.jurisdiction,
        }
    )

    # 3. Answerability Policy Check
    policy_decision = evaluate_answerability(state.routed_query, provisions=provisions)
    if not policy_decision.answerable:
        state.intent = AgentIntent.ABSTENTION
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = policy_decision.reason
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        state.add_trace(
            node_name="analyze_node",
            action="policy_rejection",
            status="abstained",
            rationale=policy_decision.explanation,
            duration_ms=duration_ms,
            output_summary=f"Abstention reason: {policy_decision.reason}",
            metadata={"reason": policy_decision.reason},
        )
        return state

    query_cf = state.query.text.casefold()

    def localized_subquery(english: str, arabic: str) -> str:
        return arabic if state.output_language == "ar" else english

    # 4. Detect Calculation Intent
    salary_match = re.search(
        r"(?:basic\s+salary|salary|راتب)\s*(?:is|=|:)?\s*(?:aed\s*)?(\d+(?:\.\d+)?)",
        query_cf,
        re.IGNORECASE,
    )
    years_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:years?\s+of\s+service|years?\s+service|سنة|سنوات)",
        query_cf,
        re.IGNORECASE,
    )
    has_calc_intent = any(
        w in query_cf for w in ("calculate", "computation", "احسب", "كم يستحق", "حساب")
    ) or (bool(salary_match and years_match))

    if has_calc_intent:
        state.intent = AgentIntent.CALCULATION
        sub_qs = [
            localized_subquery(
                "Under UAE Labour Law, how is end of service gratuity calculated under Article 51?",
                "بموجب قانون العمل الإماراتي، كيف تحسب مكافأة نهاية الخدمة وفقاً للمادة 51؟",
            )
        ]
        if bool(re.search(r"\b(part[-\s]?time|دوام\s+جزئي|عمل\s+جزئي)\b", query_cf)):
            sub_qs.append(
                localized_subquery(
                    "Under Cabinet Resolution No. 1 of 2022 Article 30, how is part-time gratuity calculated?",
                    "بموجب قرار مجلس الوزراء رقم 1 لسنة 2022 المادة 30، كيف تحسب مكافأة نهاية الخدمة للعمل الجزئي؟",
                )
            )
        state.sub_queries = sub_qs
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        state.add_trace(
            node_name="analyze_node",
            action="detect_calculation",
            status="success",
            rationale="Query contains computational request; routed to statutory basis retrieval before tool execution.",
            duration_ms=duration_ms,
            output_summary=f"Sub-queries: {state.sub_queries}",
        )
        return state

    # 5. Deterministic Multi-Hop Decomposition
    sub_queries: list[str] = []

    # Scenario A: Probation transfer + non-compete
    if any(w in query_cf for w in ("probation", "تجربة")) and any(
        w in query_cf for w in ("non-compete", "competitor", "منافس", "عدم المنافسة")
    ):
        state.intent = AgentIntent.MULTI_HOP
        sub_queries.append(
            localized_subquery(
                "Under UAE Labour Law, what notice is required to move to another employer during probation under Article 9 clause 3?",
                "بموجب قانون العمل الإماراتي، ما مدة الإخطار المطلوبة للانتقال إلى صاحب عمل آخر خلال فترة التجربة وفقاً للمادة 9 البند 3؟",
            )
        )
        sub_queries.append(
            localized_subquery(
                "Under UAE Labour Law Article 10 non-compete clause worker shall not compete with the employer in any competing project",
                "بموجب قانون العمل الإماراتي، ما أقصى مدة لشرط عدم المنافسة وفقاً للمادة 10؟",
            )
        )

    # Scenario B: Immediate probation termination + gratuity + final dues
    elif (
        any(w in query_cf for w in ("probation", "تجربة"))
        and any(w in query_cf for w in ("terminate", "without notice", "فصل", "إنهاء"))
        and any(w in query_cf for w in ("gratuity", "dues", "مستحقات", "مكافأة"))
    ):
        state.intent = AgentIntent.MULTI_HOP
        sub_queries.append(
            localized_subquery(
                "Under UAE Labour Law, how much notice must an employer give during probation under Article 9 clause 1?",
                "بموجب قانون العمل الإماراتي، ما مدة الإخطار التي يجب على صاحب العمل تقديمها خلال فترة التجربة وفقاً للمادة 9 البند 1؟",
            )
        )
        sub_queries.append(
            localized_subquery(
                "Under UAE Labour Law, when is gratuity payable under Article 51?",
                "بموجب قانون العمل الإماراتي، متى تستحق مكافأة نهاية الخدمة وفقاً للمادة 51؟",
            )
        )
        sub_queries.append(
            localized_subquery(
                "Under UAE Labour Law, within what timeframe must an employer pay worker entitlements under Article 53?",
                "بموجب قانون العمل الإماراتي، خلال أي مدة يجب على صاحب العمل سداد مستحقات العامل وفقاً للمادة 53؟",
            )
        )

    if sub_queries:
        state.sub_queries = sub_queries[: state.max_sub_queries]
    else:
        state.intent = AgentIntent.DIRECT_FACTUAL
        state.sub_queries = [state.query.text]

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="analyze_node",
        action="route_and_decompose",
        status="success",
        rationale=f"Intent classified as {state.intent.value} with {len(state.sub_queries)} sub-queries.",
        duration_ms=duration_ms,
        output_summary=f"Sub-queries: {state.sub_queries}",
        metadata={
            "intent": state.intent.value,
            "jurisdiction": state.jurisdiction.value,
        },
    )
    return state


def retrieval_node(
    state: AgentState,
    retriever: RetrieverProtocol,
    canonical_provisions: Sequence[LegalProvision],
) -> AgentState:
    """Execute hybrid retrieval for all pending sub-queries with hard metadata pre-filtering."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    if state.is_timed_out() or not state.has_step_budget():
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "exhausted_budget"
        state.add_trace(
            "retrieval_node",
            "timeout_check",
            "budget_exceeded",
            "Timeout or step budget reached.",
            0.0,
        )
        return state

    canonical_map = {p.provision_id: p for p in canonical_provisions}
    canonical_alignments = {p.alignment_id for p in canonical_provisions if p.alignment_id}

    pending_queries = [q for q in state.sub_queries if q not in state.completed_sub_queries]
    per_query_top_k = max(1, min(5, state.max_evidence_items // max(1, len(pending_queries))))
    for sub_q in state.sub_queries:
        if sub_q in state.completed_sub_queries:
            continue

        q_obj = LegalQuery(
            query_id=f"{state.query.query_id}_sub_{len(state.completed_sub_queries)}",
            text=sub_q,
            language=state.language,
            jurisdiction=state.jurisdiction,
            as_of=state.query.as_of,
        )

        try:
            hits = retriever.search(
                q_obj,
                top_k=per_query_top_k,
                filters=RetrievalFilters(
                    jurisdiction=state.jurisdiction,
                    as_of=state.query.as_of,
                    language=state.language,
                    legal_domain="employment",
                ),
            )
        except Exception as exc:
            state.errors.append(f"Retrieval failure: {type(exc).__name__}")
            state.current_status = AgentStatus.ABSTAINED
            state.abstention_reason = "retrieval_failure"
            state.add_trace(
                "retrieval_node",
                "search_and_validate",
                "failed",
                f"Retrieval backend raised {type(exc).__name__}; no partial answer will be synthesized.",
                (time.perf_counter() - start_time) * 1000.0,
            )
            return state

        for ev in hits:
            prov = ev.provision
            align_id = prov.alignment_id or prov.provision_id

            # Hard metadata and corpus validation
            canonical = canonical_map.get(prov.provision_id)
            if canonical is None:
                continue
            if canonical.model_dump(mode="json") != prov.model_dump(mode="json"):
                continue
            if align_id not in canonical_alignments:
                continue
            if prov.legal_domain != "employment":
                continue
            if not matches_retrieval_filters(
                prov,
                RetrievalFilters(
                    jurisdiction=state.jurisdiction,
                    as_of=state.query.as_of,
                    language=state.language,
                    legal_domain="employment",
                ),
            ):
                continue
            if (
                state.jurisdiction != Jurisdiction.UNKNOWN
                and prov.jurisdiction != state.jurisdiction
            ):
                continue
            if state.query.as_of is not None:
                if prov.effective_from is not None and state.query.as_of < prov.effective_from:
                    continue
                if prov.effective_until is not None and state.query.as_of > prov.effective_until:
                    continue

            # Deduplicate by alignment ID, keeping strongest
            if align_id not in state.deduplicated_alignment_ids:
                if len(state.retrieved_evidence) < state.max_evidence_items:
                    state.retrieved_evidence.append(ev)
                    state.deduplicated_alignment_ids.add(align_id)
            else:
                existing_index = next(
                    index
                    for index, item in enumerate(state.retrieved_evidence)
                    if (item.provision.alignment_id or item.provision.provision_id) == align_id
                )
                existing = state.retrieved_evidence[existing_index]
                existing_score = (
                    max(
                        value
                        for value in (
                            existing.reranker_score,
                            existing.dense_score,
                            existing.sparse_score,
                        )
                        if value is not None
                    )
                    if any(
                        value is not None
                        for value in (
                            existing.reranker_score,
                            existing.dense_score,
                            existing.sparse_score,
                        )
                    )
                    else float("-inf")
                )
                new_score = (
                    max(
                        value
                        for value in (
                            ev.reranker_score,
                            ev.dense_score,
                            ev.sparse_score,
                        )
                        if value is not None
                    )
                    if any(
                        value is not None
                        for value in (
                            ev.reranker_score,
                            ev.dense_score,
                            ev.sparse_score,
                        )
                    )
                    else float("-inf")
                )
                if new_score > existing_score:
                    state.retrieved_evidence[existing_index] = ev

        # Article-specific sub-queries need deterministic corpus expansion even
        # when lexical ranking buries the requested clause below unrelated hits.
        article_match = re.search(
            r"(?:article|المادة|للمادة)\s*\(?\s*(\d+)\s*\)?", sub_q.casefold()
        )
        clause_match = re.search(
            r"(?:clause|paragraph|البند|الفقرة)\s*\(?\s*(\d+)\s*\)?", sub_q.casefold()
        )
        if article_match:
            requested_article = article_match.group(1)
            requested_clause = clause_match.group(1) if clause_match else None
            preferred_language = "ar" if state.output_language == "ar" else "en"
            for prov in canonical_provisions:
                if prov.article != requested_article:
                    continue
                if requested_clause is not None and prov.clause != requested_clause:
                    continue
                if prov.language != preferred_language:
                    continue
                if not matches_retrieval_filters(
                    prov,
                    RetrievalFilters(
                        jurisdiction=state.jurisdiction,
                        as_of=state.query.as_of,
                        language=state.language,
                        legal_domain="employment",
                    ),
                ):
                    continue
                align_id = prov.alignment_id or prov.provision_id
                if align_id in state.deduplicated_alignment_ids:
                    continue
                if len(state.retrieved_evidence) >= state.max_evidence_items:
                    break
                state.retrieved_evidence.append(
                    LegalEvidence(
                        evidence_id=f"ev-{prov.provision_id}",
                        provision=prov,
                        retrieval_rank=len(state.retrieved_evidence) + 1,
                    )
                )
                state.deduplicated_alignment_ids.add(align_id)

        state.completed_sub_queries.append(sub_q)

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="retrieval_node",
        action="search_and_validate",
        status="success",
        rationale=f"Retrieved {len(state.retrieved_evidence)} canonical evidence items across {len(state.completed_sub_queries)} sub-queries.",
        duration_ms=duration_ms,
        output_summary=f"Alignments: {sorted(state.deduplicated_alignment_ids)}",
        metadata={"evidence_count": len(state.retrieved_evidence)},
    )
    return state


def tool_node(state: AgentState) -> AgentState:
    """Execute statutory calculation tools gated by verified legal basis in retrieved evidence."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    # Tool execution must only proceed if legal basis is present in retrieved evidence
    gratuity_basis = "FDL_33_2021_A51_C2"
    if gratuity_basis not in state.deduplicated_alignment_ids:
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "missing_legal_basis"
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        state.add_trace(
            node_name="tool_node",
            action="gated_check",
            status="skipped",
            rationale=f"Statutory basis '{gratuity_basis}' absent from evidence; tool execution prohibited.",
            duration_ms=duration_ms,
        )
        return state

    query_cf = state.query.text.casefold()
    salary_match = re.search(
        r"(?:basic\s+salary|salary|راتب)\s*(?:is|=|:)?\s*(?:aed\s*)?(\d+(?:\.\d+)?)",
        query_cf,
        re.IGNORECASE,
    )
    years_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:years?\s+of\s+service|years?\s+service|سنة|سنوات)",
        query_cf,
        re.IGNORECASE,
    )

    if salary_match and years_match:
        try:
            salary_val = Decimal(salary_match.group(1))
            years_val = Decimal(years_match.group(1))

            if (
                not salary_val.is_finite()
                or not years_val.is_finite()
                or salary_val <= Decimal("0")
                or years_val < Decimal("0")
            ):
                state.errors.append("Invalid tool input: values must be positive and finite.")
                state.current_status = AgentStatus.ABSTAINED
                state.abstention_reason = "invalid_tool_input"
                return state

            # 1. Worker Nationality Status: Expatriate/Foreign Worker vs UAE National (GPSSA Pension)
            is_uae_national: bool | None = None
            if re.search(
                r"\b(emirati|uae\s+national|local\s+citizen|national\s+citizen|مواطن|إماراتي)\b",
                query_cf,
            ):
                is_uae_national = True
            elif re.search(
                r"\b(foreign\s+worker|foreign\s+employee|foreign\s+national|expatriate|expat|non-uae\s+national|non-national|non-emirati|وافد|عامل\s+وافد|موظف\s+وافد|أجنبي|غير\s+مواطن)\b",
                query_cf,
            ):
                is_uae_national = False

            # 2. Work Pattern: Full-time under Article 51(2) vs Part-time under Cabinet Res 1/2022 Article 30
            work_pattern: Literal["full_time", "part_time"] | None = None
            if re.search(r"\b(part[-\s]?time|دوام\s+جزئي|عمل\s+جزئي)\b", query_cf):
                work_pattern = "part_time"
            elif re.search(r"\b(full[-\s]?time|دوام\s+كامل|عمل\s+كامل)\b", query_cf):
                work_pattern = "full_time"

            # 3. Fact Completeness Gate: Silent factual assumptions are prohibited under legal safety policy
            missing_facts: list[str] = []
            if is_uae_national is None:
                missing_facts.append(
                    "worker nationality status (foreign worker eligible under Article 51 vs UAE national covered by GPSSA pension under Article 51(1))"
                )
            if work_pattern is None:
                missing_facts.append(
                    "work pattern (full-time under Article 51(2) vs part-time under Cabinet Resolution No. 1 of 2022 Article 30)"
                )

            if missing_facts:
                state.current_status = AgentStatus.ABSTAINED
                state.abstention_reason = "missing_eligibility_facts"
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                state.add_trace(
                    node_name="tool_node",
                    action="fact_completeness_check",
                    status="abstained",
                    rationale=(
                        f"Statutory calculation cannot proceed due to missing factual prerequisites: "
                        f"{'; '.join(missing_facts)}. Under UAE Labour Law, silent assumptions on "
                        "nationality and work pattern are prohibited."
                    ),
                    duration_ms=duration_ms,
                    metadata={"missing_facts": missing_facts},
                )
                return state

            assert is_uae_national is not None
            assert work_pattern is not None

            # 4. Extract Contractual Working Hours for Part-Time Workers
            contract_working_hours: Decimal | None = None
            full_time_working_hours: Decimal | None = None
            if work_pattern == "part_time":
                ratio_match = re.search(
                    r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs)?\s*(?:out\s+of|\/)\s*(\d+(?:\.\d+)?)\s*(?:full[-\s]?time\s*)?(?:hours?|hrs)?",
                    query_cf,
                )
                if ratio_match:
                    contract_working_hours = Decimal(ratio_match.group(1))
                    full_time_working_hours = Decimal(ratio_match.group(2))
                else:
                    c_match = re.search(
                        r"(\d+(?:\.\d+)?)\s*(?:contract(?:ual)?\s*hours?|hours?\s*(?:per\s*week|weekly|in\s*contract)|ساعات\s*العمل\s*التعاقدية|ساعة\s*تعاقدية)",
                        query_cf,
                    )
                    ft_match = re.search(
                        r"(\d+(?:\.\d+)?)\s*(?:full[-\s]?time\s*hours?|hours?\s*full[-\s]?time|ساعات\s*الدوام\s*الكامل)",
                        query_cf,
                    )
                    if c_match and ft_match:
                        contract_working_hours = Decimal(c_match.group(1))
                        full_time_working_hours = Decimal(ft_match.group(1))

            absence_match = re.search(
                r"(?:unpaid\s+leave|unpaid\s+absence|غياب\s+بدون\s+راتب|إجازة\s+بدون\s+راتب)\s*(?:is|=|:)?\s*(\d+(?:\.\d+)?)",
                query_cf,
            )
            unpaid_absence_days = Decimal(absence_match.group(1)) if absence_match else Decimal("0")

            for prior_call in state.tool_calls:
                if (
                    prior_call.tool_name == "calculate_gratuity"
                    and prior_call.valid_schema
                    and prior_call.arguments.get("basic_salary") == float(salary_val)
                    and prior_call.arguments.get("service_years") == float(years_val)
                    and prior_call.arguments.get("is_uae_national") == is_uae_national
                    and prior_call.arguments.get("work_pattern") == work_pattern
                    and prior_call.arguments.get("unpaid_absence_days")
                    == float(unpaid_absence_days)
                    and prior_call.arguments.get("contract_working_hours")
                    == (
                        float(contract_working_hours)
                        if contract_working_hours is not None
                        else None
                    )
                    and prior_call.arguments.get("full_time_working_hours")
                    == (
                        float(full_time_working_hours)
                        if full_time_working_hours is not None
                        else None
                    )
                ):
                    state.tool_results = prior_call.result or {}
                    state.add_trace(
                        node_name="tool_node",
                        action="idempotency_check",
                        status="skipped",
                        rationale="The same validated gratuity inputs were already executed.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                    return state

            # Validate input bounds
            GratuityInput(
                basic_salary=salary_val,
                service_years=years_val,
                work_pattern=work_pattern,
                is_uae_national=is_uae_national,
                unpaid_absence_days=unpaid_absence_days,
                contract_working_hours=contract_working_hours,
                full_time_working_hours=full_time_working_hours,
            )

            calc_res = calculate_gratuity(
                salary_val,
                years_val,
                is_uae_national=is_uae_national,
                work_pattern=work_pattern,
                unpaid_absence_days=unpaid_absence_days,
                contract_working_hours=contract_working_hours,
                full_time_working_hours=full_time_working_hours,
            )
            exec_latency_ms = (time.perf_counter() - start_time) * 1000.0

            basis_used = calc_res.get("statutory_basis", gratuity_basis)
            tool_call = ToolCall(
                tool_name="calculate_gratuity",
                arguments={
                    "basic_salary": float(salary_val),
                    "service_years": float(years_val),
                    "is_uae_national": is_uae_national,
                    "work_pattern": work_pattern,
                    "unpaid_absence_days": float(unpaid_absence_days),
                    "contract_working_hours": (
                        float(contract_working_hours)
                        if contract_working_hours is not None
                        else None
                    ),
                    "full_time_working_hours": (
                        float(full_time_working_hours)
                        if full_time_working_hours is not None
                        else None
                    ),
                },
                result=calc_res,
                valid_schema=True,
                statutory_basis=basis_used,
                execution_latency_ms=exec_latency_ms,
            )
            state.tool_calls.append(tool_call)
            state.tool_results = calc_res

            # Handle non-calculable part-time gratuity due to missing hours
            if calc_res.get("total_gratuity") is None:
                state.current_status = AgentStatus.ABSTAINED
                state.abstention_reason = "missing_contractual_hours"
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                state.add_trace(
                    node_name="tool_node",
                    action="execute_gratuity",
                    status="abstained",
                    rationale=(
                        "Part-time gratuity entitlement is non-calculable without contractual and "
                        "full-time working hours under Cabinet Resolution No. 1 of 2022 Article 30."
                    ),
                    duration_ms=duration_ms,
                    output_summary=f"Result: {calc_res}",
                    metadata={"eligibility_status": calc_res["eligibility_status"]},
                )
                return state

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            state.add_trace(
                node_name="tool_node",
                action="execute_gratuity",
                status="success",
                rationale=f"Calculated statutory gratuity AED {calc_res['total_gratuity']} anchored to {basis_used}.",
                duration_ms=duration_ms,
                output_summary=f"Result: {calc_res}",
                metadata={"total_gratuity": calc_res["total_gratuity"]},
            )
            return state
        except Exception as exc:
            state.errors.append(f"Tool execution failed: {type(exc).__name__}")
            state.current_status = AgentStatus.ABSTAINED
            state.abstention_reason = "invalid_tool_input"

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="tool_node",
        action="extract_inputs",
        status="no_action",
        rationale="Computational inputs could not be validated from query text.",
        duration_ms=duration_ms,
    )
    state.current_status = AgentStatus.ABSTAINED
    state.abstention_reason = "invalid_tool_input"
    return state


def synthesis_node(state: AgentState, generator: GeneratorProtocol) -> AgentState:
    """Synthesize citation-grounded draft answer from evidence and validated tool results."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    if state.is_timed_out() or not state.has_step_budget():
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "exhausted_budget"
        state.add_trace(
            "synthesis_node",
            "timeout_check",
            "budget_exceeded",
            "Budget exceeded.",
            0.0,
        )
        return state

    try:
        draft = generator.generate(
            query=state.routed_query or state.query,
            evidence=state.retrieved_evidence,
            jurisdiction=state.jurisdiction,
            language=Language(state.output_language),
            tool_calls=state.tool_calls,
        )
        if state.is_timed_out() or not state.has_step_budget():
            state.draft_answer = None
            state.current_status = AgentStatus.ABSTAINED
            state.abstention_reason = "exhausted_budget"
            state.add_trace(
                "synthesis_node",
                "timeout_check",
                "budget_exceeded",
                "Timeout exceeded during generator execution.",
                (time.perf_counter() - start_time) * 1000.0,
            )
            return state
        state.draft_answer = draft
    except Exception as exc:
        state.errors.append(f"Generator failure: {type(exc).__name__}")
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "provider_failure"
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        state.add_trace(
            node_name="synthesis_node",
            action="generate_draft",
            status="failed",
            rationale=f"Generator raised {type(exc).__name__}. Safely routed to abstention.",
            duration_ms=duration_ms,
        )
        return state

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="synthesis_node",
        action="generate_draft",
        status="success",
        rationale=f"Synthesized draft with {len(draft.claims)} claims.",
        duration_ms=duration_ms,
        output_summary=draft.summary[:150],
        metadata={"claims_count": len(draft.claims)},
    )
    return state


def verification_node(state: AgentState, verifier: CitationVerifier) -> AgentState:
    """Execute strict verification gate on draft claims and citations."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1
    state.verification_attempts += 1

    if not state.draft_answer:
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "unverified_claim"
        state.add_trace(
            node_name="verification_node",
            action="verify_claims",
            status="failed",
            rationale="No draft answer was available for verification.",
            duration_ms=(time.perf_counter() - start_time) * 1000.0,
        )
        return state

    grounded = verifier.verify(
        query=state.routed_query or state.query,
        draft=state.draft_answer,
        evidence=state.retrieved_evidence,
    )
    state.verified_answer = grounded

    # Tool consistency validation: verify calculated claims match tool output
    if state.tool_results and grounded.claims:
        total_grat = state.tool_results.get("total_gratuity")
        expected_total = Decimal(str(total_grat)) if total_grat is not None else None
        for vc in grounded.claims:
            if (
                vc.tool_derived
                and vc.claim_key == "gratuity_calculation"
                and vc.verification_status == VerificationStatus.VERIFIED
            ):
                number_matches = {
                    Decimal(value) for value in re.findall(r"\b\d+(?:\.\d+)?\b", vc.conclusion)
                }
                if expected_total is None or expected_total not in number_matches:
                    vc.verification_status = VerificationStatus.CONTRADICTED
                    vc.verification_reasons.append(
                        "Claim numeric value does not match validated tool calculation."
                    )
                    grounded.answerable = False
                    grounded.verification_status = VerificationStatus.CONTRADICTED
                    grounded.abstention_reason = "contradictory_evidence"
                    grounded.full_text = "**Abstention Notice**: Claim numeric value contradicts statutory tool calculation."
                    grounded.summary = "Contradicted tool calculation."

    duration_ms = (time.perf_counter() - start_time) * 1000.0

    if grounded.verification_status == VerificationStatus.VERIFIED:
        state.current_status = AgentStatus.VERIFIED
        status_str = "verified"
    elif grounded.verification_status == VerificationStatus.CONTRADICTED:
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "contradictory_evidence"
        status_str = "contradicted"
    elif grounded.verification_status == VerificationStatus.ABSTAINED:
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = grounded.abstention_reason or "policy_abstention"
        status_str = "abstained"
    else:
        status_str = "unverified"
        if state.verification_attempts < state.max_attempts and not state.is_timed_out():
            state.current_status = AgentStatus.RETRYING
        else:
            state.current_status = AgentStatus.ABSTAINED
            state.abstention_reason = "unverified_claim"

    state.add_trace(
        node_name="verification_node",
        action="verify_claims",
        status=status_str,
        rationale=f"Verification status: {grounded.verification_status.value} (Attempt {state.verification_attempts}/{state.max_attempts}).",
        duration_ms=duration_ms,
        output_summary=f"Answerable: {grounded.answerable}, Status: {grounded.verification_status.value}",
        metadata={
            "status": grounded.verification_status.value,
            "attempt": state.verification_attempts,
        },
    )
    return state


def reformulate_node(
    state: AgentState, canonical_provisions: Sequence[LegalProvision]
) -> AgentState:
    """Analyze unverified claims, formulate targeted sub-queries, and schedule re-retrieval."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    if (
        state.verification_attempts >= state.max_attempts
        or state.is_timed_out()
        or not state.has_step_budget()
    ):
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "exhausted_budget"
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        state.add_trace(
            "reformulate_node",
            "budget_check",
            "abstained",
            "Exhausted retry budget.",
            duration_ms,
        )
        return state

    # Identify unverified claims
    unverified_claims = [
        c
        for c in (state.verified_answer.claims if state.verified_answer else [])
        if c.verification_status == VerificationStatus.UNVERIFIED
    ]

    canonical_alignments = {p.alignment_id for p in canonical_provisions if p.alignment_id}
    reformulated = False

    for claim in unverified_claims:
        for cid in claim.cited_alignment_ids:
            if cid in canonical_alignments and cid not in state.visited_reformulation_keys:
                state.visited_reformulation_keys.add(cid)
                # Formulate targeted statutory query
                art_match = re.search(r"_A(\d+)", cid)
                art_num = art_match.group(1) if art_match else ""
                target_sub = f"Under UAE Labour Law, what are the specific rules of Article {art_num} for {claim.claim_key}?"
                if len(state.sub_queries) < state.max_sub_queries:
                    state.sub_queries.append(target_sub)
                    reformulated = True

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    if reformulated:
        state.current_status = AgentStatus.RETRIEVING
        state.add_trace(
            node_name="reformulate_node",
            action="expand_sub_queries",
            status="retry_scheduled",
            rationale=f"Scheduled re-retrieval for unverified provisions: {state.visited_reformulation_keys}.",
            duration_ms=duration_ms,
            output_summary=f"Sub-queries expanded to {len(state.sub_queries)} items.",
        )
    else:
        state.current_status = AgentStatus.ABSTAINED
        state.abstention_reason = "unverified_claim"
        state.add_trace(
            node_name="reformulate_node",
            action="no_recoverable_provisions",
            status="abstained",
            rationale="No recoverable canonical provisions found for unverified claims.",
            duration_ms=duration_ms,
        )

    return state


def abstain_node(state: AgentState, verifier: CitationVerifier) -> AgentState:
    """Produce formal, citation-bounded abstention response."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1
    reason = state.abstention_reason or "policy_abstention"

    state.verified_answer = GroundedLegalAnswer(
        answer_id=f"ans-abstain-{state.query.query_id}",
        query=state.routed_query or state.query,
        jurisdiction=state.jurisdiction,
        answerable=False,
        verification_status=VerificationStatus.ABSTAINED,
        summary="The query cannot be legally answered within the supported UAE legal corpus.",
        claims=[],
        full_text="**Abstention Notice**: The query cannot be legally answered within the supported UAE legal corpus.",
        referenced_evidence=[],
        tool_calls=state.tool_calls,
        abstention_reason=reason,
    )
    state.current_status = AgentStatus.ABSTAINED

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="abstain_node",
        action="emit_abstention",
        status="abstained",
        rationale=f"Emitted verified abstention for reason: '{reason}'.",
        duration_ms=duration_ms,
        output_summary="Abstention notice emitted.",
        metadata={"reason": reason},
    )
    return state


def finalize_node(state: AgentState) -> LegalResponse:
    """Map final agent state into canonical LegalResponse with complete audit trace."""
    start_time = time.perf_counter()
    state.step_count += 1
    state.execution_steps += 1

    ans = state.verified_answer

    # Strict multi-gate verification check
    ver_passed = bool(
        ans
        and ans.answerable
        and (ans.verification_status == VerificationStatus.VERIFIED)
        and all(vc.verification_status == VerificationStatus.VERIFIED for vc in ans.claims)
        and bool(ans.claims)
    )

    candidate_evidence = ans.referenced_evidence if (ans and ans.referenced_evidence) else []
    jurisdiction_valid = (state.jurisdiction == Jurisdiction.UAE_ONSHORE) and (
        all(ev.provision.jurisdiction == state.jurisdiction for ev in candidate_evidence)
    )
    temporal_valid = all(
        (
            state.query.as_of is None
            or (
                (
                    ev.provision.effective_from is None
                    or state.query.as_of >= ev.provision.effective_from
                )
                and (
                    ev.provision.effective_until is None
                    or state.query.as_of <= ev.provision.effective_until
                )
            )
        )
        for ev in candidate_evidence
    )

    overall_passed = ver_passed and jurisdiction_valid and temporal_valid

    if not overall_passed:
        reason = (
            state.abstention_reason
            or (ans.abstention_reason if ans else None)
            or ("unsupported_jurisdiction" if not jurisdiction_valid else None)
            or ("temporal_out_of_scope" if not temporal_valid else None)
            or "unverified_claim"
        )
        if ans and ans.full_text and ans.full_text.startswith("**Abstention Notice**"):
            answer_text = ans.full_text
        else:
            answer_text = f"**Abstention Notice**: The query could not be verified against the official legal corpus ({reason})."
        answerable = False
        claims: list[Claim] = [
            Claim(
                claim_id=c.claim_id,
                text=c.conclusion,
                claim_key=c.claim_key,
                cited_alignment_ids=c.cited_alignment_ids,
                evidence_ids=[],
                supported=False,
            )
            for c in (ans.claims if ans else [])
        ]
        evidence: list[LegalEvidence] = []
        ver_result = VerificationResult(
            passed=False,
            citation_valid=False,
            claims_grounded=False,
            jurisdiction_valid=jurisdiction_valid,
            temporal_valid=temporal_valid,
            bilingual_consistent=None,
            schema_valid=True,
            issues=(
                [r for claim in (ans.claims if ans else []) for r in claim.verification_reasons]
                + state.errors
                + [reason]
            ),
        )
        confidence = 0.0
        fallback_reason = reason
    else:
        assert ans is not None
        answer_text = ans.full_text
        answerable = True
        evidence = ans.referenced_evidence

        # Map synthesis claims to canonical Claim objects
        evidence_id_by_align: dict[str, list[str]] = {}
        for ev in state.retrieved_evidence:
            align_id = ev.provision.alignment_id or ev.provision.provision_id
            evidence_id_by_align.setdefault(align_id, []).append(ev.evidence_id)

        claims = []
        for vc in ans.claims:
            ev_ids = []
            for cid in vc.cited_alignment_ids:
                ev_ids.extend(evidence_id_by_align.get(cid, []))
            claims.append(
                Claim(
                    claim_id=vc.claim_id,
                    text=vc.conclusion,
                    claim_key=vc.claim_key,
                    cited_alignment_ids=vc.cited_alignment_ids,
                    evidence_ids=ev_ids,
                    supported=True,
                )
            )

        ver_result = VerificationResult(
            passed=True,
            citation_valid=True,
            claims_grounded=True,
            jurisdiction_valid=jurisdiction_valid,
            temporal_valid=temporal_valid,
            bilingual_consistent=None,
            schema_valid=True,
            issues=state.errors,
        )
        confidence = min([claim.support_score for claim in ans.claims] or [1.0])
        fallback_reason = None

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    state.add_trace(
        node_name="finalize_node",
        action="build_legal_response",
        status="completed",
        rationale=f"Built canonical LegalResponse with answerable={answerable}.",
        duration_ms=duration_ms,
        output_summary=f"Claims: {len(claims)}, Evidence: {len(evidence)}, Tools: {len(state.tool_calls)}",
    )

    return LegalResponse(
        query_id=state.query.query_id,
        answerable=answerable,
        answer=answer_text,
        language=state.output_language,
        jurisdiction=state.jurisdiction,
        evidence=evidence,
        claims=claims,
        tool_calls=state.tool_calls,
        verification=ver_result,
        confidence=confidence,
        fallback_reason=fallback_reason,
        trace=state.trace,
        created_at=datetime.utcnow(),
    )
