from __future__ import annotations

import asyncio
import os
import time
from typing import Any, cast
from collections.abc import Awaitable, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Overwrite, interrupt

from drugagent.evidence.artifact_store import apply_retention_policy, artifact_layout
from drugagent.scoring.conflicts import analyze_conflicts
from drugagent.evidence.dossier import persist_evidence_dossier
from drugagent.scoring.sufficiency import (
    assess_evidence_sufficiency,
    maybe_apply_auto_recollect,
    resolve_auto_recollect_policy,
)
from drugagent.evidence.graph import build_evidence_graph_snapshot, persist_evidence_graph_snapshot
from drugagent.memory.episodic import persist_episodic_memory, query_episodic_memory
from drugagent.observability.health import persist_health_report, run_source_health_checks, validate_source_health
from drugagent.observability.metrics import build_run_metrics, persist_run_metrics
from drugagent.data.mcp_runtime import collect_source_via_mcp_with_raw
from drugagent.agents.input_validator import InputValidationAgent
from drugagent.agents.normalization_agent import NormalizationAgent
from drugagent.scoring.normalizer import normalize_evidence_items
from drugagent.agents.planner import build_collection_plan, persist_collection_plan, selected_sources_for_request
from drugagent.memory.procedural import persist_procedural_memory
from interfaces.review import persist_review_audit, load_review_decision
from interfaces.plan import load_plan_decision
from drugagent.agents.reviewer import ReviewSupportAgent
from drugagent.data.server_manager import ExternalServerContext
from drugagent.agents.summarizer import SummaryAgent
from drugagent.agents.supervisor import SupervisorAgent
from drugagent.observability.telemetry import log_event
from drugagent.agents.verifier import run_verification
from drugagent.memory.working import persist_working_memory_snapshot
from interfaces.run_state import RunStateStore
from drugagent.scoring.agent import NormalizationScoringAgent
from drugagent.scoring.schemas import SourceEvidence
from drugagent.evidence.visualizer import generate_evidence_html

from drugagent.core.schema import (
    CollectorRequest,
    CollectorResult,
    CollectionPlan,
    ErrorCode,
    ErrorRecord,
    AgentReport,
    EvidenceDossier,
    Phase2HandoffPayload,
    PlanDecisionStatus,
    ReviewDecisionStatus,
    SourceStatus,
    SourceName,
    StatusName,
    SupervisorAction,
    SupervisorDecision,
)
from drugagent.core.state import CollectorState


COLLECTOR_NODE_SEQUENCE = [
    "validate_input",
    "plan_collection",
    "plan_review_gate",
    "collect_sources_parallel",
    "normalize_evidence",
    "verify_evidence",
    "analyze_conflicts",
    "score_evidence",
    "assess_sufficiency",
    "build_evidence_graph",
    "generate_explanation",
    "supervisor_decide",
    "prepare_review_brief",
    "human_review_gate",
    "emit_dossier",
]

COLLECTOR_CHECKPOINTER = MemorySaver()
STATIC_NEXT_STAGE = {
    "validate_input": "plan_collection",
    "plan_collection": "plan_review_gate",
    "plan_review_gate": "collect_sources_parallel",
    "collect_sources_parallel": "normalize_evidence",
    "normalize_evidence": "verify_evidence",
    "verify_evidence": "analyze_conflicts",
    "analyze_conflicts": "score_evidence",
    "score_evidence": "assess_sufficiency",
    "assess_sufficiency": "build_evidence_graph",
    "build_evidence_graph": "generate_explanation",
    "generate_explanation": "supervisor_decide",
    "prepare_review_brief": "human_review_gate",
    "emit_dossier": "__end__",
}


class CollectionPaused(Exception):
    def __init__(
        self,
        *,
        run_id: str,
        next_stages: tuple[str, ...],
        state_values: dict[str, Any],
        reason: str = "workflow_paused",
    ) -> None:
        super().__init__(f"Collection paused for run `{run_id}` at {', '.join(next_stages) or 'unknown stage'}.")
        self.run_id = run_id
        self.next_stages = next_stages
        self.state_values = state_values
        self.reason = reason


def _notify_progress(progress_cb: Callable[[str, dict[str, Any]], None] | None, event: str, **payload: Any) -> None:
    if progress_cb is None:
        return
    progress_cb(event, payload)


def _summarize_progress_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            # Keep key fields for plan display in CLI traces.
            if "planning_mode" in dumped:
                keep = [
                    "run_id",
                    "selected_sources",
                    "query_intent",
                    "query_variants",
                    "memory_context",
                    "source_directives",
                    "planning_mode",
                    "planner_model_used",
                    "artifact_path",
                ]
                return {key: dumped.get(key) for key in keep if key in dumped}
            return {key: dumped[key] for key in list(dumped)[:8]}
        return dumped
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {key: _summarize_progress_value(val) for key, val in list(value.items())[:8]}
    return value


def _progress_state_snapshot(state: CollectorState) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key, value in state.items():
        snapshot[key] = _summarize_progress_value(value)
    return snapshot


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)

def _source_timeout_s() -> float:
    raw = os.getenv("A4T_SOURCE_TIMEOUT_S", "45").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 45.0


def _state_run_id(state: CollectorState) -> str:
    query = state.get("query")
    if isinstance(query, CollectorRequest):
        return query.run_id
    if isinstance(query, dict):
        return str(query.get("run_id", "unknown-run"))
    return "unknown-run"


def _wrap_stage(
    stage_name: str,
    node: Callable[[CollectorState], Awaitable[dict]],
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
) -> Callable[[CollectorState], Awaitable[dict]]:
    async def wrapped(state: CollectorState) -> dict:
        run_id = _state_run_id(state)
        started_at = time.perf_counter()
        log_event("stage_start", run_id=run_id, stage=stage_name)
        _notify_progress(
            progress_cb,
            "stage_start",
            run_id=run_id,
            stage=stage_name,
            state=_progress_state_snapshot(state),
        )
        try:
            update = await node(state)
            persist_working_memory_snapshot(run_id, stage_name, {"state": state, "update": update})
            next_hint = STATIC_NEXT_STAGE.get(stage_name)
            RunStateStore.write_latest(
                run_id,
                stage=stage_name,
                state=cast(dict[str, Any], state),
                update=update,
                next_stages=(next_hint,) if next_hint and next_hint != "__end__" else (),
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            log_event(
                "stage_end",
                run_id=run_id,
                stage=stage_name,
                success=True,
                duration_ms=duration_ms,
            )
            _notify_progress(
                progress_cb,
                "stage_end",
                run_id=run_id,
                stage=stage_name,
                duration_ms=duration_ms,
                update=_summarize_progress_value(update),
            )
            next_stage = STATIC_NEXT_STAGE.get(stage_name)
            if next_stage is not None:
                _notify_progress(
                    progress_cb,
                    "edge",
                    run_id=run_id,
                    from_stage=stage_name,
                    to_stage=next_stage,
                    reason="static_transition",
                )
            return update
        except Exception as exc:  # noqa: BLE001
            # LangGraph interrupts are resumable pauses (human-in-the-loop) and should not be treated
            # as stage errors in observability/UI.
            if isinstance(exc, GraphInterrupt):
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                persist_working_memory_snapshot(
                    run_id,
                    stage_name,
                    {"state": state, "interrupt": str(exc)},
                )
                RunStateStore.write_latest(
                    run_id,
                    stage=stage_name,
                    state=cast(dict[str, Any], state),
                    update=None,
                    next_stages=(),
                    status="paused",
                    error=str(exc),
                )
                log_event(
                    "stage_end",
                    run_id=run_id,
                    stage=stage_name,
                    success=True,
                    duration_ms=duration_ms,
                    interrupted=True,
                )
                _notify_progress(
                    progress_cb,
                    "stage_end",
                    run_id=run_id,
                    stage=stage_name,
                    duration_ms=duration_ms,
                    update={"paused": True},
                )
                raise
            persist_working_memory_snapshot(
                run_id,
                f"{stage_name}_error",
                {"state": state, "error": str(exc)},
            )
            RunStateStore.write_latest(
                run_id,
                stage=stage_name,
                state=cast(dict[str, Any], state),
                update=None,
                next_stages=(),
                status="failed",
                error=str(exc),
            )
            setattr(exc, "__drugagent_stage__", stage_name)
            setattr(exc, "__drugagent_run_id__", run_id)
            setattr(exc, "__drugagent_input__", _progress_state_snapshot(state))
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            log_event(
                "stage_end",
                run_id=run_id,
                stage=stage_name,
                success=False,
                duration_ms=duration_ms,
                error=str(exc),
            )
            _notify_progress(
                progress_cb,
                "stage_error",
                run_id=run_id,
                stage=stage_name,
                duration_ms=duration_ms,
                error=str(exc),
                state=_progress_state_snapshot(state),
            )
            raise

    return wrapped


async def _collect_single_source_safe(
    source: SourceName,
    request: CollectorRequest,
    collector=collect_source_via_mcp_with_raw,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[list, SourceStatus, list[ErrorRecord], dict]:
    started_at = time.perf_counter()
    _notify_progress(
        progress_cb,
        "source_start",
        run_id=request.run_id,
        source=source.value,
    )
    try:
        items, status, errors, raw_payload = await asyncio.wait_for(
            collector(source, request),
            timeout=_source_timeout_s(),
        )
        _notify_progress(
            progress_cb,
            "source_end",
            run_id=request.run_id,
            source=source.value,
            status=status.status.value if hasattr(status.status, "value") else str(status.status),
            record_count=status.record_count,
            duration_ms=status.duration_ms,
            error=str(status.error_message or ""),
        )
        return items, status, errors, raw_payload
    except asyncio.TimeoutError:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        message = f"Collector timeout after {_source_timeout_s():.0f}s for {source.value}."
        status = SourceStatus(
            source=source,
            status=StatusName.FAILED,
            duration_ms=duration_ms,
            record_count=0,
            error_code=ErrorCode.TIMEOUT,
            error_message=message,
        )
        errors = [
            ErrorRecord(
                source=source,
                error_code=ErrorCode.TIMEOUT,
                message=message,
                retryable=True,
            )
        ]
        raw_payload = {
            "run_id": request.run_id,
            "source": source.value,
            "query": request.model_dump(mode="json"),
            "items": [],
            "source_status": [status.model_dump(mode="json")],
            "errors": [error.model_dump(mode="json") for error in errors],
        }
        _notify_progress(
            progress_cb,
            "source_end",
            run_id=request.run_id,
            source=source.value,
            status=StatusName.FAILED.value,
            record_count=0,
            duration_ms=duration_ms,
            error=message,
        )
        return [], status, errors, raw_payload
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        message = f"Collector crashed for {source.value}: {exc}"
        status = SourceStatus(
            source=source,
            status=StatusName.FAILED,
            duration_ms=duration_ms,
            record_count=0,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message=message,
        )
        errors = [
            ErrorRecord(
                source=source,
                error_code=ErrorCode.INTERNAL_ERROR,
                message=message,
                retryable=False,
            )
        ]
        raw_payload = {
            "run_id": request.run_id,
            "source": source.value,
            "query": request.model_dump(mode="json"),
            "items": [],
            "source_status": [status.model_dump(mode="json")],
            "errors": [error.model_dump(mode="json") for error in errors],
        }
        _notify_progress(
            progress_cb,
            "source_end",
            run_id=request.run_id,
            source=source.value,
            status=StatusName.FAILED.value,
            record_count=0,
            duration_ms=duration_ms,
            error=message,
        )
        return [], status, errors, raw_payload


async def collect_sources_in_parallel(
    request: CollectorRequest,
    plan=None,
    collector=collect_source_via_mcp_with_raw,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict:
    planned_sources = getattr(plan, "selected_sources", None) if plan is not None else None
    selected_sources = (
        [source if isinstance(source, SourceName) else SourceName(source) for source in planned_sources]
        if planned_sources
        else selected_sources_for_request(request)
    )

    if not selected_sources:
        return {
            "evidence_items": [],
            "source_status": [],
            "errors": [],
            "raw_source_payloads": [],
            "source_agent_reports": [],
        }

    dispatch_mode = os.getenv("A4T_SOURCE_DISPATCH_MODE", "parallel").strip().lower()
    sequential = dispatch_mode in {"sequential", "serial", "one_by_one", "one-by-one"}

    if sequential:
        results = []
        for source in selected_sources:
            results.append(await _collect_single_source_safe(source, request, collector=collector, progress_cb=progress_cb))
    else:
        tasks = [
            _collect_single_source_safe(source, request, collector=collector, progress_cb=progress_cb)
            for source in selected_sources
        ]
        results = await asyncio.gather(*tasks)

    all_items = []
    all_status = []
    all_errors = []
    all_raw_payloads = []
    source_agent_reports: list[AgentReport] = []
    directives = getattr(plan, "source_directives", {}) if plan is not None else {}
    for source, (items, status, errors, raw_payload) in zip(selected_sources, results, strict=False):
        all_items.extend(items)
        all_status.append(status)
        all_errors.extend(errors)
        all_raw_payloads.append(raw_payload)
        _ = directives  # directives are still persisted in the plan; no per-source LLM reviews in lean flow.

    return {
        "evidence_items": all_items,
        "source_status": all_status,
        "errors": all_errors,
        "raw_source_payloads": all_raw_payloads,
        "source_agent_reports": source_agent_reports,
    }


def build_collector_graph(progress_cb: Callable[[str, dict[str, Any]], None] | None = None) -> StateGraph:
    async def validate_input_node(state: CollectorState):
        query = state.get("query")
        if query is None:
            raise ValueError("CollectorState missing 'query'")
        if isinstance(query, dict):
            query = CollectorRequest.model_validate(query)
            
        # Sanitize model_override: if it contains 'gpt-', nullify it so get_llm uses the system default (Gemini).
        if query.model_override and "gpt-" in query.model_override.lower():
            query = query.model_copy(update={"model_override": None})
            
        # Episodic memory is primarily gene-target centric. Always load the gene-level history
        # so planning can leverage prior runs even when disease context differs or is missing.
        past_runs = query_episodic_memory(gene_symbol=query.gene_symbol)
        report = await InputValidationAgent(model=query.model_override).review(request=query, past_run_count=len(past_runs))
        _notify_progress(
            progress_cb,
            "agent_report",
            run_id=query.run_id,
            stage_name=report.stage_name,
            agent_name=report.agent_name,
            generation_mode=report.generation_mode,
            model_used=report.model_used,
            summary=report.summary,
        )
        
        return {
            "query": query,
            "past_runs": past_runs,
            "input_validation_report": report,
            "review_iteration_count": state.get("review_iteration_count", 0),
            "review_recollection_pending": state.get("review_recollection_pending", False),
        }

    async def plan_collection_node(state: CollectorState):
        query = state["query"]
        plan = persist_collection_plan(await build_collection_plan(query, past_runs=state.get("past_runs", [])))
        persist_procedural_memory(query.run_id, COLLECTOR_NODE_SEQUENCE)
        planning_report = AgentReport(
            agent_name="planning_agent",
            stage_name="plan_collection",
            summary=plan.query_intent,
            decisions=[
                f"Selected sources: {', '.join(_enum_value(source) for source in plan.selected_sources) or 'none'}",
                "Prepared per-source directives for collection agents.",
            ],
            risks=[],
            next_actions=["Execute planned source collection in planner-defined order."],
            structured_payload=plan.model_dump(mode="json"),
            generation_mode=plan.planning_mode,
            model_used=plan.planner_model_used,
        )
        _notify_progress(
            progress_cb,
            "agent_report",
            run_id=query.run_id,
            stage_name=planning_report.stage_name,
            agent_name=planning_report.agent_name,
            generation_mode=planning_report.generation_mode,
            model_used=planning_report.model_used,
            summary=planning_report.summary,
        )
        return {"plan": plan, "planning_report": planning_report, "plan_decision": state.get("plan_decision")}

    async def plan_review_gate_node(state: CollectorState):
        query = state["query"]
        plan = state.get("plan")
        if plan is None:
            raise ValueError("plan_review_gate requires state.plan")

        require_plan_approval = os.getenv("A4T_REQUIRE_PLAN_APPROVAL", "0").strip().lower() in {"1", "true", "yes"}
        plan_decision = state.get("plan_decision")
        if plan_decision is None:
            plan_decision = load_plan_decision(query.run_id)

        if plan_decision is None:
            if require_plan_approval:
                return interrupt(
                    {
                        "run_id": query.run_id,
                        "reason": "plan_approval_required",
                        "plan": plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan,
                        "planning_report": (
                            state["planning_report"].model_dump(mode="json")
                            if state.get("planning_report") is not None and hasattr(state["planning_report"], "model_dump")
                            else None
                        ),
                    }
                )
            return {"plan_decision": None}

        if plan_decision.decision == PlanDecisionStatus.APPROVED:
            return {"plan_decision": plan_decision}

        if plan_decision.decision == PlanDecisionStatus.NEEDS_CHANGES:
            if not plan_decision.updated_plan:
                return interrupt(
                    {
                        "run_id": query.run_id,
                        "reason": "plan_changes_requested_missing_patch",
                        "plan": plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan,
                        "message": "Decision was needs_changes but no updated_plan provided.",
                    }
                )
            updated = dict(plan_decision.updated_plan)
            updated.setdefault("run_id", query.run_id)
            updated_plan = CollectionPlan.model_validate(updated)
            updated_plan = persist_collection_plan(updated_plan)
            return {"plan": updated_plan, "plan_decision": plan_decision}

        if plan_decision.decision == PlanDecisionStatus.REJECTED:
            raise RuntimeError(f"Run `{query.run_id}` plan was rejected by reviewer `{plan_decision.reviewer_id}`.")

        return {"plan_decision": plan_decision}

    async def collect_sources_parallel_node(state: CollectorState):
        query = state["query"]
        plan = state.get("plan")
        planned_sources = getattr(plan, "selected_sources", None) if plan is not None else None
        selected_sources = (
            [source if isinstance(source, SourceName) else SourceName(source) for source in planned_sources]
            if planned_sources
            else selected_sources_for_request(query)
        )

        # If no sources are requested/planned but evidence is already seeded in state (tests, offline runs),
        # preserve that evidence rather than overwriting it with an empty collection result.
        if not selected_sources and state.get("evidence_items"):
            return {
                "source_status": state.get("source_status", []),
                "errors": state.get("errors", []),
                "raw_source_payloads": state.get("raw_source_payloads", []),
                "source_agent_reports": state.get("source_agent_reports", []),
            }

        return await collect_sources_in_parallel(query, plan=plan, progress_cb=progress_cb)

    async def normalize_evidence_node(state: CollectorState):
        query = state["query"]
        normalized_items = normalize_evidence_items(state.get("evidence_items", []))
        report = await NormalizationAgent(model=query.model_override).review(
            raw_count=len(state.get("evidence_items", [])),
            normalized_items=normalized_items,
        )
        _notify_progress(
            progress_cb,
            "agent_report",
            run_id=_state_run_id(state),
            stage_name=report.stage_name,
            agent_name=report.agent_name,
            generation_mode=report.generation_mode,
            model_used=report.model_used,
            summary=report.summary,
        )
        return {"normalized_items": normalized_items, "normalization_report": report}

    async def verify_evidence_node(state: CollectorState):
        query = state["query"]
        verification_report = run_verification(
            query,
            state.get("normalized_items", []),
            source_status=state.get("source_status", []),
        )
        return {"verification_report": verification_report, "verification_agent_report": None}

    async def analyze_conflicts_node(state: CollectorState):
        query = state["query"]
        conflicts = analyze_conflicts(state.get("normalized_items", []))
        _ = query
        return {"conflicts": conflicts, "conflict_agent_report": None}

    async def assess_sufficiency_node(state: CollectorState):
        query = state["query"]
        verification = state.get("verification_report")
        conflicts = state.get("conflicts", [])

        sufficiency = assess_evidence_sufficiency(state.get("normalized_items", []))

        high_conflict = any(getattr(c, "severity", None) == "high" for c in (conflicts or []))
        blocked = bool(getattr(verification, "blocked", False)) if verification is not None else False
        has_sources = bool(getattr(query, "sources", None)) and len(getattr(query, "sources", []) or []) > 0

        policy = resolve_auto_recollect_policy()
        auto_count = int(state.get("auto_recollect_count", 0) or 0)
        decision = (
            maybe_apply_auto_recollect(
                per_source_top_k=int(query.per_source_top_k),
                max_literature_articles=int(query.max_literature_articles),
                sufficiency=sufficiency,
                blocked=blocked,
                high_conflict=high_conflict,
                auto_recollect_count=auto_count,
                max_passes=int(policy["max_passes"]),
                top_k_step=int(policy["top_k_step"]),
                lit_step=int(policy["lit_step"]),
            )
            if has_sources
            else {
                "should_recollect": False,
                "next_per_source_top_k": int(query.per_source_top_k),
                "next_max_literature_articles": int(query.max_literature_articles),
                "next_auto_recollect_count": auto_count,
            }
        )

        if bool(decision["should_recollect"]):
            updated_query = query.model_copy(
                update={
                    "per_source_top_k": int(decision["next_per_source_top_k"]),
                    "max_literature_articles": int(decision["next_max_literature_articles"]),
                }
            )

            rationale = "Auto recollect triggered: " + (" ".join(sufficiency.reasons) if sufficiency.reasons else "insufficient evidence.")
            _notify_progress(
                progress_cb,
                "agent_decision",
                run_id=query.run_id,
                stage_name="assess_sufficiency",
                agent_name="sufficiency_agent",
                action="recollect_evidence",
                rationale=rationale,
                decision_mode="deterministic_sufficiency",
                model_used=None,
            )

            return {
                "query": updated_query,
                "evidence_sufficiency": sufficiency,
                "auto_recollect_count": int(decision["next_auto_recollect_count"]),
                "auto_recollect_pending": True,
                "evidence_items": Overwrite([]),
                "normalized_items": Overwrite([]),
                "source_status": Overwrite([]),
                "errors": Overwrite([]),
                "raw_source_payloads": Overwrite([]),
                "source_agent_reports": Overwrite([]),
                "normalization_report": None,
                "verification_agent_report": None,
                "conflict_agent_report": None,
                "graph_agent_report": None,
                "summary_agent_report": None,
                "conflicts": Overwrite([]),
                "evidence_graph": None,
                "explanation": "",
                "final_result": None,
            }

        return {"evidence_sufficiency": sufficiency, "auto_recollect_pending": False}

    async def score_evidence_node(state: CollectorState):
        query = state["query"]
        items = state.get("normalized_items", [])
        source_status = state.get("source_status", [])
        
        # 1. Group records by source
        by_source: dict[str, list[Any]] = {}
        for item in items:
            src = str(item.source.value if hasattr(item.source, "value") else item.source)
            if src not in by_source:
                by_source[src] = []
            by_source[src].append(item)
            
        status_map = {str(s.source.value if hasattr(s.source, "value") else s.source): s for s in source_status}
        
        # 2. Build SourceEvidence list
        evidence_list = []
        scoring_sources = ["pharos", "depmap", "open_targets", "literature"]
        upstream_aliases = {
            "pharos": ("pharos", "ext_pharos"),
            "depmap": ("depmap",),
            # NOTE: internally we use `opentargets` as a SourceName value, but the
            # scoring agent + evidence dashboard expect `open_targets`.
            "open_targets": ("opentargets", "ext_opentargets", "open_targets"),
            "literature": ("literature",),
        }

        def _pick_records_and_status(scoring_src: str):
            for upstream in upstream_aliases.get(scoring_src, (scoring_src,)):
                records = by_source.get(upstream, [])
                status = status_map.get(upstream)
                if records or status:
                    return upstream, records, status
            return scoring_src, [], None

        for scoring_key in scoring_sources:
            _upstream_name, records, status = _pick_records_and_status(scoring_key)
            
            if not records and not status:
                continue
                
            data_present = len(records) > 0
            total_available = len(records)
            
            raw_signal = None
            raw_signal_label = None
            metadata = {}
            
            if scoring_key == "pharos" and records:
                # Pharos: extract TDL and ligand_total from first record raw_value
                first_raw = records[0].raw_value
                if isinstance(first_raw, dict):
                    tdl = first_raw.get("tdl")
                    raw_signal = tdl
                    metadata["tdl"] = tdl
                    metadata["ligand_total"] = first_raw.get("ligand_total")
                total_available = 1
                raw_signal_label = "TDL"
                    
            elif scoring_key == "depmap" and records:
                # DepMap: prefer average_gene_effect when available; total_available=cell_line_count.
                sup = records[0].support if isinstance(records[0].support, dict) else {}
                raw_signal = sup.get("average_gene_effect", records[0].raw_value)
                if isinstance(sup.get("cell_line_count"), (int, float)):
                    total_available = int(sup.get("cell_line_count") or 0)
                raw_signal_label = "CERES"
                
            elif scoring_key == "open_targets" and records:
                # Open Targets: raw_value is the score (0-1)
                raw_signal = records[0].raw_value
                # Collect all disease scores for max() normalization
                all_scores = [float(r.raw_value) for r in records if isinstance(r.raw_value, (int, float))]
                metadata["all_disease_scores"] = all_scores
                metadata["requested_disease_id"] = query.disease_id
                metadata["disease_scores"] = [
                    {
                        "disease_id": r.disease_id,
                        "score": float(r.raw_value) if isinstance(r.raw_value, (int, float)) else None,
                        "summary": r.summary,
                    }
                    for r in records[:10]
                ]
                # Prefer the upstream evidence_count field as total_available (total associations).
                try:
                    counts = []
                    for r in records:
                        sup = r.support if isinstance(r.support, dict) else {}
                        if isinstance(sup.get("evidence_count"), (int, float)):
                            counts.append(int(sup.get("evidence_count") or 0))
                    if counts:
                        total_available = max(counts)
                except Exception:
                    pass
                raw_signal_label = "association_score"
                
            elif scoring_key == "literature":
                # Literature: raw_signal is citations from first record, 
                # but scoring uses total paper count (hit count).
                try:
                    hit_counts = []
                    eligible_counts = []
                    for r in records:
                        sup = r.support if isinstance(r.support, dict) else {}
                        if isinstance(sup.get("total_hit_count"), (int, float)):
                            hit_counts.append(int(sup.get("total_hit_count") or 0))
                        if isinstance(sup.get("eligible_hit_count"), (int, float)):
                            eligible_counts.append(int(sup.get("eligible_hit_count") or 0))
                    if hit_counts:
                        metadata["total_hit_count"] = max(hit_counts)
                    if eligible_counts:
                        total_available = max(eligible_counts)
                    else:
                        total_available = 0
                except Exception:
                    pass
                raw_signal = total_available
                raw_signal_label = "paper_count"
                    
            evidence_list.append(SourceEvidence(
                source=scoring_key,
                gene=query.gene_symbol,
                data_present=data_present,
                total_available=total_available,
                top_k_results=[r.model_dump() for r in records[:5]],
                raw_signal=float(raw_signal) if isinstance(raw_signal, (int, float)) else raw_signal,
                raw_signal_label=raw_signal_label,
                metadata=metadata
            ))
            
        # 3. Score
        agent = NormalizationScoringAgent()
        scored_target = agent.score(evidence_list)

        # 4. Evidence dashboard artifact
        dashboard_path: str | None = None
        try:
            dashboard_path = artifact_layout(query.run_id).get("evidence_dashboard")
            if dashboard_path:
                generate_evidence_html([scored_target], dashboard_path)
        except Exception as exc:  # noqa: BLE001
            scored_target.notes.append(f"Evidence dashboard generation failed: {exc}")

        return {"scored_target": scored_target, "evidence_dashboard_path": dashboard_path}

    async def build_evidence_graph_node(state: CollectorState):
        query = state["query"]
        snapshot = build_evidence_graph_snapshot(
            state.get("normalized_items", []),
            conflicts=state.get("conflicts", []),
        )
        persisted_snapshot = persist_evidence_graph_snapshot(query.run_id, snapshot)
        return {"evidence_graph": persisted_snapshot, "graph_agent_report": None}

    async def generate_explanation_node(state: CollectorState):
        query = state["query"]
        items = state.get("normalized_items", [])
        summary_agent = SummaryAgent(model=query.model_override)
        llm_summary = await summary_agent.run(
            request=query,
            items=items,
            source_status=state.get("source_status", []),
            verification_report=state.get("verification_report"),
            conflicts=state.get("conflicts", []),
            evidence_graph=state.get("evidence_graph"),
            scored_target=state.get("scored_target"),
            evidence_dashboard_path=state.get("evidence_dashboard_path"),
        )
        _notify_progress(
            progress_cb,
            "agent_report",
            run_id=query.run_id,
            stage_name="generate_explanation",
            agent_name="summary_agent",
            generation_mode=llm_summary.generation_mode,
            model_used=llm_summary.model_used,
            summary=f"Generated evidence summary for {query.gene_symbol}.",
        )
        summary_report = AgentReport(
            agent_name="summary_agent",
            stage_name="generate_explanation",
            summary=f"Generated evidence summary for {query.gene_symbol}.",
            decisions=["Synthesized grounded findings from verified evidence only."],
            risks=[],
            next_actions=["Provide summary context to supervisor and review-support agents."],
            structured_payload={
                "generation_mode": llm_summary.generation_mode,
                "model_used": llm_summary.model_used,
            },
            generation_mode=llm_summary.generation_mode,
            model_used=llm_summary.model_used,
        )
        return {"explanation": llm_summary.markdown_report, "final_result": CollectorResult(
            run_id=query.run_id,
            query=query,
            items=items,
            source_status=state.get("source_status", []),
            errors=state.get("errors", []),
            llm_summary=llm_summary,
        ), "summary_agent_report": summary_report}

    async def supervisor_decide_node(state: CollectorState):
        query = state["query"]
        review_iteration_count = state.get("review_iteration_count", 0)
        existing_review = state.get("review_decision")

        # If a human already provided a decision, honor it before the supervisor tries to recollect/re-route.
        # - APPROVED/REJECTED: proceed to dossier.
        # - NEEDS_MORE_EVIDENCE: allow a single recollection loop.
        if existing_review is not None:
            if existing_review.decision in {ReviewDecisionStatus.APPROVED, ReviewDecisionStatus.REJECTED}:
                supervisor_decision = SupervisorDecision(
                    action=SupervisorAction.EMIT_DOSSIER,
                    rationale="Existing human review decision present; proceed to dossier emission.",
                    confidence=0.95,
                    follow_up_actions=["Persist the human review decision in the dossier."],
                    decision_mode="human_override",
                    model_used=None,
                )
                return {
                    "supervisor_decision": supervisor_decision,
                    "review_brief": None,
                    "review_decision": existing_review,
                    "review_recollection_pending": False,
                    "review_iteration_count": review_iteration_count,
                }

            if (
                existing_review.decision == ReviewDecisionStatus.NEEDS_MORE_EVIDENCE
                and review_iteration_count < 1
                and not state.get("review_recollection_pending", False)
            ):
                supervisor_decision = SupervisorDecision(
                    action=SupervisorAction.RECOLLECT_EVIDENCE,
                    rationale="Human reviewer requested more evidence; schedule one recollection pass.",
                    confidence=0.9,
                    follow_up_actions=["Re-run collection once to broaden evidence coverage."],
                    decision_mode="human_override",
                    model_used=None,
                )
                return {
                    "supervisor_decision": supervisor_decision,
                    "review_brief": None,
                    "review_decision": existing_review,
                    "review_iteration_count": review_iteration_count + 1,
                    "review_recollection_pending": True,
                    "evidence_items": Overwrite([]),
                    "normalized_items": Overwrite([]),
                    "source_status": Overwrite([]),
                    "errors": Overwrite([]),
                    "raw_source_payloads": Overwrite([]),
                    "source_agent_reports": Overwrite([]),
                    "normalization_report": None,
                    "verification_agent_report": None,
                    "conflict_agent_report": None,
                    "graph_agent_report": None,
                    "summary_agent_report": None,
                    "conflicts": Overwrite([]),
                }

        latest_memory = None
        past_runs = state.get("past_runs", [])
        if past_runs:
            latest_memory = (past_runs[-1].get("review_decision") or {}).get("decision")
        supervisor_agent = SupervisorAgent(model=query.model_override)
        supervisor_decision = await supervisor_agent.decide(
            request=query,
            source_status=state.get("source_status", []),
            verification_report=state["verification_report"],
            conflicts=state.get("conflicts", []),
            evidence_count=len(state.get("normalized_items", [])),
            review_iteration_count=review_iteration_count,
            latest_memory_decision=latest_memory,
            agent_reports=[
                *([state["input_validation_report"]] if state.get("input_validation_report") is not None else []),
                *([state["planning_report"]] if state.get("planning_report") is not None else []),
                *state.get("source_agent_reports", []),
                *([state["normalization_report"]] if state.get("normalization_report") is not None else []),
                *([state["verification_agent_report"]] if state.get("verification_agent_report") is not None else []),
                *([state["conflict_agent_report"]] if state.get("conflict_agent_report") is not None else []),
                *([state["graph_agent_report"]] if state.get("graph_agent_report") is not None else []),
                *([state["summary_agent_report"]] if state.get("summary_agent_report") is not None else []),
            ],
        )
        _notify_progress(
            progress_cb,
            "agent_decision",
            run_id=query.run_id,
            stage_name="supervisor_decide",
            agent_name="supervisor_agent",
            action=_enum_value(supervisor_decision.action),
            rationale=supervisor_decision.rationale,
            decision_mode=supervisor_decision.decision_mode,
            model_used=supervisor_decision.model_used,
        )
        if (
            supervisor_decision.action == SupervisorAction.RECOLLECT_EVIDENCE
            and review_iteration_count < 1
        ):
            return {
                "supervisor_decision": supervisor_decision,
                "review_brief": None,
                "review_decision": None,
                "review_iteration_count": review_iteration_count + 1,
                "review_recollection_pending": True,
                "evidence_items": Overwrite([]),
                "normalized_items": Overwrite([]),
                "source_status": Overwrite([]),
                "errors": Overwrite([]),
                "raw_source_payloads": Overwrite([]),
                "source_agent_reports": Overwrite([]),
                "normalization_report": None,
                "verification_agent_report": None,
                "conflict_agent_report": None,
                "graph_agent_report": None,
                "summary_agent_report": None,
                "conflicts": Overwrite([]),
            }
        return {
            "supervisor_decision": supervisor_decision,
            "review_brief": None,
            "review_decision": state.get("review_decision"),
            "review_recollection_pending": False,
            "review_iteration_count": review_iteration_count,
        }

    async def prepare_review_brief_node(state: CollectorState):
        query = state["query"]
        review_support_agent = ReviewSupportAgent(model=query.model_override)
        review_brief = await review_support_agent.build(
            request=query,
            source_status=state.get("source_status", []),
            verification_report=state["verification_report"],
            conflicts=state.get("conflicts", []),
            explanation=state.get("explanation", ""),
        )
        _notify_progress(
            progress_cb,
            "agent_report",
            run_id=query.run_id,
            stage_name="prepare_review_brief",
            agent_name="review_support_agent",
            generation_mode=review_brief.generation_mode,
            model_used=review_brief.model_used,
            summary=review_brief.summary,
        )
        return {"review_brief": review_brief}

    async def human_review_gate_node(state: CollectorState):
        query = state["query"]
        review_decision = state.get("review_decision")
        if review_decision is None:
            review_decision = load_review_decision(query.run_id)
        supervisor_decision = state.get("supervisor_decision")
        require_review = os.getenv("A4T_REQUIRE_REVIEW", "1").strip().lower() not in {"0", "false", "no"}
        supervisor_force_enabled = os.getenv("A4T_SUPERVISOR_FORCE_REVIEW", "0").strip().lower() in {"1", "true", "yes"}
        forced_review = bool(
            supervisor_force_enabled
            and supervisor_decision is not None
            and supervisor_decision.action == SupervisorAction.REQUEST_HUMAN_REVIEW
        )
        review_iteration_count = state.get("review_iteration_count", 0)

        if review_decision is None:
            if require_review or forced_review:
                return interrupt(
                    {
                        "run_id": query.run_id,
                        "reason": "review_required" if require_review else "supervisor_requested_review",
                        "verification_blocked": state["verification_report"].blocked,
                        "conflict_count": len(state.get("conflicts", [])),
                        "supervisor_decision": (
                            supervisor_decision.model_dump(mode="json") if supervisor_decision is not None else None
                        ),
                        "review_brief": (
                            state["review_brief"].model_dump(mode="json")
                            if state.get("review_brief") is not None
                            else None
                        ),
                    }
                )
            return {
                "review_decision": None,
                "review_recollection_pending": False,
                "review_iteration_count": review_iteration_count,
            }

        if (
            review_decision.decision == ReviewDecisionStatus.NEEDS_MORE_EVIDENCE
            and review_iteration_count < 1
        ):
            return {
                "review_decision": review_decision,
                "review_iteration_count": review_iteration_count + 1,
                "review_recollection_pending": True,
                "evidence_items": Overwrite([]),
                "normalized_items": Overwrite([]),
                "source_status": Overwrite([]),
                "errors": Overwrite([]),
                "raw_source_payloads": Overwrite([]),
                "source_agent_reports": Overwrite([]),
                "normalization_report": None,
                "verification_agent_report": None,
                "conflict_agent_report": None,
                "graph_agent_report": None,
                "summary_agent_report": None,
                "conflicts": Overwrite([]),
            }

        return {
            "review_decision": review_decision,
            "review_iteration_count": review_iteration_count,
            "review_recollection_pending": False,
        }

    async def emit_dossier_node(state: CollectorState):
        query = state["query"]
        final_result = state["final_result"]
        dashboard_path = state.get("evidence_dashboard_path") or artifact_layout(query.run_id).get("evidence_dashboard") or ""
        handoff_payload = Phase2HandoffPayload(
            run_id=query.run_id,
            ready=(
                state.get("review_decision") is not None
                and state["review_decision"].decision == ReviewDecisionStatus.APPROVED
            ),
            review_required=(
                state.get("review_decision") is None
                or state["review_decision"].decision != ReviewDecisionStatus.APPROVED
            ),
            blocking_issues=state["verification_report"].blocking_issues,
            warning_issues=state["verification_report"].warning_issues,
            conflict_count=len(state.get("conflicts", [])),
            evidence_count=len(state.get("normalized_items", [])),
            requested_source_count=len(state["plan"].selected_sources),
            successful_source_count=sum(
                1
                for status in state.get("source_status", [])
                if status.status == StatusName.SUCCESS
            ),
            graph_artifact_path=state["evidence_graph"].artifact_path,
            reason=(
                "approved"
                if state.get("review_decision") and state["review_decision"].decision == ReviewDecisionStatus.APPROVED
                else "rejected_by_reviewer"
                if state.get("review_decision") and state["review_decision"].decision == ReviewDecisionStatus.REJECTED
                else "needs_more_evidence"
                if state.get("review_decision") and state["review_decision"].decision == ReviewDecisionStatus.NEEDS_MORE_EVIDENCE
                else "verification_blocked"
                if state["verification_report"].blocked
                else "awaiting_human_review"
            ),
        )
        dossier = EvidenceDossier(
            run_id=query.run_id,
            query=query,
            run_metadata={
                "requested_sources": len(state["plan"].selected_sources),
                "model_override": query.model_override,
                "collector_node_sequence": COLLECTOR_NODE_SEQUENCE,
                "review_iteration_count": state.get("review_iteration_count", 0),
            },
            source_status=state.get("source_status", []),
            errors=state.get("errors", []),
            plan=state["plan"],
            verified_evidence=state.get("normalized_items", []),
            verification_report=state["verification_report"],
            conflicts=state.get("conflicts", []),
            graph_snapshot=state["evidence_graph"],
            review_decision=state.get("review_decision"),
            summary_markdown=state.get("explanation", ""),
            artifacts={
                "plan": state["plan"].artifact_path or "",
                "graph": state["evidence_graph"].artifact_path or "",
                "evidence_dashboard": dashboard_path,
            },
            handoff_payload=handoff_payload,
        )
        dossier_report = None
        artifact_path = persist_evidence_dossier(dossier)
        final_dossier = dossier.model_copy(
            update={
                "artifact_path": artifact_path,
                "handoff_payload": dossier.handoff_payload.model_copy(update={"dossier_artifact_path": artifact_path}),
            }
        )
        persist_evidence_dossier(final_dossier)
        if final_dossier.review_decision is not None:
            persist_review_audit(query.run_id, final_dossier.review_decision)
        persist_episodic_memory(final_dossier)
        if os.getenv("A4T_RETENTION_ENABLED", "0").strip().lower() in {"1", "true", "yes"}:
            keep = {query.run_id}
            extra = os.getenv("A4T_RETENTION_KEEP_RUNS", "")
            for run_id in [token.strip() for token in extra.split(",") if token.strip()]:
                keep.add(run_id)
            apply_retention_policy(retain_run_ids=keep)

        return {"final_result": final_result, "final_dossier": final_dossier, "dossier_agent_report": dossier_report}

    graph_builder = StateGraph(CollectorState)
    graph_builder.add_node("validate_input", cast(Any, _wrap_stage("validate_input", validate_input_node, progress_cb=progress_cb)))
    graph_builder.add_node("plan_collection", cast(Any, _wrap_stage("plan_collection", plan_collection_node, progress_cb=progress_cb)))
    graph_builder.add_node("plan_review_gate", cast(Any, _wrap_stage("plan_review_gate", plan_review_gate_node, progress_cb=progress_cb)))
    graph_builder.add_node("collect_sources_parallel", cast(Any, _wrap_stage("collect_sources_parallel", collect_sources_parallel_node, progress_cb=progress_cb)))
    graph_builder.add_node("normalize_evidence", cast(Any, _wrap_stage("normalize_evidence", normalize_evidence_node, progress_cb=progress_cb)))
    graph_builder.add_node("verify_evidence", cast(Any, _wrap_stage("verify_evidence", verify_evidence_node, progress_cb=progress_cb)))
    graph_builder.add_node("analyze_conflicts", cast(Any, _wrap_stage("analyze_conflicts", analyze_conflicts_node, progress_cb=progress_cb)))
    graph_builder.add_node("score_evidence", cast(Any, _wrap_stage("score_evidence", score_evidence_node, progress_cb=progress_cb)))
    graph_builder.add_node("assess_sufficiency", cast(Any, _wrap_stage("assess_sufficiency", assess_sufficiency_node, progress_cb=progress_cb)))
    graph_builder.add_node("build_evidence_graph", cast(Any, _wrap_stage("build_evidence_graph", build_evidence_graph_node, progress_cb=progress_cb)))
    graph_builder.add_node("generate_explanation", cast(Any, _wrap_stage("generate_explanation", generate_explanation_node, progress_cb=progress_cb)))
    graph_builder.add_node("supervisor_decide", cast(Any, _wrap_stage("supervisor_decide", supervisor_decide_node, progress_cb=progress_cb)))
    graph_builder.add_node("prepare_review_brief", cast(Any, _wrap_stage("prepare_review_brief", prepare_review_brief_node, progress_cb=progress_cb)))
    graph_builder.add_node("human_review_gate", cast(Any, _wrap_stage("human_review_gate", human_review_gate_node, progress_cb=progress_cb)))
    graph_builder.add_node("emit_dossier", cast(Any, _wrap_stage("emit_dossier", emit_dossier_node, progress_cb=progress_cb)))

    graph_builder.add_edge(START, "validate_input")
    graph_builder.add_edge("validate_input", "plan_collection")
    graph_builder.add_edge("plan_collection", "plan_review_gate")
    graph_builder.add_edge("plan_review_gate", "collect_sources_parallel")
    graph_builder.add_edge("collect_sources_parallel", "normalize_evidence")
    graph_builder.add_edge("normalize_evidence", "verify_evidence")
    graph_builder.add_edge("verify_evidence", "analyze_conflicts")
    graph_builder.add_edge("analyze_conflicts", "score_evidence")
    graph_builder.add_edge("score_evidence", "assess_sufficiency")

    def route_after_sufficiency(state: CollectorState) -> str:
        if state.get("auto_recollect_pending", False):
            _notify_progress(
                progress_cb,
                "edge",
                run_id=_state_run_id(state),
                from_stage="assess_sufficiency",
                to_stage="plan_collection",
                reason=f"auto_recollect_pass_{state.get('auto_recollect_count', 0)}",
            )
            return "plan_collection"
        _notify_progress(
            progress_cb,
            "edge",
            run_id=_state_run_id(state),
            from_stage="assess_sufficiency",
            to_stage="build_evidence_graph",
            reason="sufficient_or_maxed",
        )
        return "build_evidence_graph"

    graph_builder.add_conditional_edges(
        "assess_sufficiency",
        route_after_sufficiency,
        {
            "plan_collection": "plan_collection",
            "build_evidence_graph": "build_evidence_graph",
        },
    )
    graph_builder.add_edge("build_evidence_graph", "generate_explanation")
    
    def route_after_supervisor(state: CollectorState) -> str:
        supervisor_decision = state.get("supervisor_decision")
        run_id = _state_run_id(state)
        
        # 1. Handle Recollection (explicit supervisor choice or fallback)
        if (
            supervisor_decision
            and supervisor_decision.action == SupervisorAction.RECOLLECT_EVIDENCE
        ):
            _notify_progress(
                progress_cb,
                "edge",
                run_id=run_id,
                from_stage="supervisor_decide",
                to_stage="plan_collection",
                reason=_enum_value(supervisor_decision.action),
            )
            return "plan_collection"

        review_decision = state.get("review_decision")
        if (
            review_decision is not None
            and review_decision.decision in {ReviewDecisionStatus.APPROVED, ReviewDecisionStatus.REJECTED}
        ):
            # Stages are bypassed; persist placeholders and synthetic logs for observability completeness.
            for stage in ("prepare_review_brief", "human_review_gate"):
                log_event("stage_start", run_id=run_id, stage=stage)
                persist_working_memory_snapshot(
                    run_id,
                    stage,
                    {"skipped": True, "reason": "existing_review_decision", "state": _progress_state_snapshot(state)},
                )
                log_event("stage_end", run_id=run_id, stage=stage, success=True, duration_ms=0, skipped=True)
            _notify_progress(
                progress_cb,
                "edge",
                run_id=run_id,
                from_stage="supervisor_decide",
                to_stage="emit_dossier",
                reason="existing_review_decision",
            )
            return "emit_dossier"

        # 2. Review Gate Routing
        # Global override via environment variable
        require_review_env = os.getenv("A4T_REQUIRE_REVIEW", "1").strip().lower() not in {"0", "false", "no"}
        
        # UI path: if review is disabled, never pause for review and never build the review brief.
        # Emit the dossier directly (still logging placeholder stages for observability completeness).
        if not require_review_env:
            for stage in ("prepare_review_brief", "human_review_gate"):
                log_event("stage_start", run_id=run_id, stage=stage)
                persist_working_memory_snapshot(
                    run_id,
                    stage,
                    {"skipped": True, "reason": "review_bypassed", "state": _progress_state_snapshot(state)},
                )
                log_event("stage_end", run_id=run_id, stage=stage, success=True, duration_ms=0, skipped=True)
            _notify_progress(
                progress_cb,
                "edge",
                run_id=run_id,
                from_stage="supervisor_decide",
                to_stage="emit_dossier",
                reason="review_disabled_globally",
            )
            return "emit_dossier"

        # 3. Default path: prepare brief then pause for review
        _notify_progress(
            progress_cb,
            "edge",
            run_id=run_id,
            from_stage="supervisor_decide",
            to_stage="prepare_review_brief",
            reason=(
                _enum_value(supervisor_decision.action)
                if supervisor_decision is not None
                else "default_review_path"
            ),
        )
        return "prepare_review_brief"

    graph_builder.add_edge("generate_explanation", "supervisor_decide")
    graph_builder.add_conditional_edges(
        "supervisor_decide",
        route_after_supervisor,
        {
            "plan_collection": "plan_collection",
            "prepare_review_brief": "prepare_review_brief",
            "emit_dossier": "emit_dossier",
        },
    )

    graph_builder.add_edge("prepare_review_brief", "human_review_gate")

    def route_after_review(state: CollectorState) -> str:
        review_decision = state.get("review_decision")
        if (
            review_decision
            and review_decision.decision == ReviewDecisionStatus.NEEDS_MORE_EVIDENCE
            and state.get("review_recollection_pending", False)
        ):
            _notify_progress(
                progress_cb,
                "edge",
                run_id=_state_run_id(state),
                from_stage="human_review_gate",
                to_stage="plan_collection",
                reason=_enum_value(review_decision.decision),
            )
            return "plan_collection"
        _notify_progress(
            progress_cb,
            "edge",
            run_id=_state_run_id(state),
            from_stage="human_review_gate",
            to_stage="emit_dossier",
            reason=(
                _enum_value(review_decision.decision)
                if review_decision is not None
                else "default_emit"
            ),
        )
        return "emit_dossier"

    graph_builder.add_conditional_edges(
        "human_review_gate",
        route_after_review,
        {
            "plan_collection": "plan_collection",
            "emit_dossier": "emit_dossier",
        },
    )
    graph_builder.add_edge("emit_dossier", END)

    return graph_builder


def _run_config(run_id: str) -> dict:
    # LangGraph enforces a recursion/step limit per invocation. In real runs (auto-recollect,
    # supervisor-driven recollection, human-in-the-loop resumes) the default can be too low and
    # cause the workflow to stop early with `next` stages remaining.
    raw_limit = os.getenv("A4T_GRAPH_RECURSION_LIMIT", "60").strip()
    try:
        recursion_limit = max(10, int(raw_limit))
    except ValueError:
        recursion_limit = 60
    return {"configurable": {"thread_id": run_id}, "recursion_limit": recursion_limit}


def get_collector_checkpointer() -> MemorySaver:
    return COLLECTOR_CHECKPOINTER


def create_collector_graph(
    checkpointer: MemorySaver | None = None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
):
    return build_collector_graph(progress_cb=progress_cb).compile(checkpointer=checkpointer or COLLECTOR_CHECKPOINTER)


def create_agent_graph(*_args, **_kwargs):
    return create_collector_graph()


async def run_collection_graph(
    request: CollectorRequest,
    connectors=None,
    progress_cb=None,
) -> CollectorResult:
    _ = connectors
    health_results = run_source_health_checks(request)
    persist_health_report(request.run_id, health_results)
    validate_source_health(request)
    app = create_collector_graph(progress_cb=progress_cb)
    config = _run_config(request.run_id)

    async with ExternalServerContext(sources=request.sources):
        await app.ainvoke({"query": request}, config=config)
        snapshot = await app.aget_state(config)
        result_state = snapshot.values

    if "final_dossier" not in result_state:
        _notify_progress(
            progress_cb,
            "workflow_paused",
            run_id=request.run_id,
            next_stages=tuple(snapshot.next),
            reason=(
                "plan_approval_required"
                if "plan_review_gate" in snapshot.next
                else "human_review_required"
                if "human_review_gate" in snapshot.next
                else "workflow_paused"
            ),
            state=_progress_state_snapshot(result_state),
        )
        raise CollectionPaused(
            run_id=request.run_id,
            next_stages=tuple(snapshot.next),
            state_values=result_state,
            reason=(
                "plan_approval_required"
                if "plan_review_gate" in snapshot.next
                else "human_review_required"
                if "human_review_gate" in snapshot.next
                else "workflow_paused"
            ),
        )

    persist_run_metrics(
        build_run_metrics(
            run_id=request.run_id,
            source_status=result_state.get("source_status", []),
            verification_report=result_state["verification_report"],
            conflicts=result_state.get("conflicts", []),
        )
    )

    return result_state["final_result"]


async def resume_collection_graph(
    request: CollectorRequest,
    connectors=None,
    progress_cb=None,
) -> CollectorResult:
    _ = connectors
    app = create_collector_graph(progress_cb=progress_cb)
    config = _run_config(request.run_id)

    async with ExternalServerContext(sources=request.sources):
        await app.ainvoke(Command(resume={}), config=config)
        snapshot = await app.aget_state(config)
        result_state = snapshot.values

    if "final_dossier" not in result_state:
        _notify_progress(
            progress_cb,
            "workflow_paused",
            run_id=request.run_id,
            next_stages=tuple(snapshot.next),
            reason=(
                "plan_approval_required"
                if "plan_review_gate" in snapshot.next
                else "human_review_required"
                if "human_review_gate" in snapshot.next
                else "workflow_paused"
            ),
            state=_progress_state_snapshot(result_state),
        )
        raise CollectionPaused(
            run_id=request.run_id,
            next_stages=tuple(snapshot.next),
            state_values=result_state,
            reason=(
                "plan_approval_required"
                if "plan_review_gate" in snapshot.next
                else "human_review_required"
                if "human_review_gate" in snapshot.next
                else "workflow_paused"
            ),
        )

    persist_run_metrics(
        build_run_metrics(
            run_id=request.run_id,
            source_status=result_state.get("source_status", []),
            verification_report=result_state["verification_report"],
            conflicts=result_state.get("conflicts", []),
        )
    )

    return result_state["final_result"]


async def get_collection_state(run_id: str):
    app = create_collector_graph()
    try:
        snapshot = await app.aget_state(_run_config(run_id))
        if getattr(snapshot, "values", None) or getattr(snapshot, "next", None):
            return snapshot
    except Exception:
        snapshot = None

    persisted = RunStateStore.load_latest(run_id)
    if persisted is not None:
        return persisted.as_snapshot()

    # Fallback for brand-new run_ids before the first stage snapshot is persisted.
    if snapshot is not None:
        return snapshot
    return type("FallbackSnapshot", (), {"values": {}, "next": ("validate_input",)})()


async def get_collection_state_history(run_id: str) -> list:
    app = create_collector_graph()
    history = []
    async for snapshot in app.aget_state_history(_run_config(run_id)):
        history.append(snapshot)
    return history
