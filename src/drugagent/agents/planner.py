from __future__ import annotations

import json
import os
import time
import hashlib
from pathlib import Path
from typing import Any

from drugagent.agents.planning_agent import PlanningAgent
from drugagent.agents.planning_agent import PlanningResponse
from drugagent.memory.semantic import gene_aliases
from drugagent.core.schema import CollectionPlan, CollectorRequest, SourceName


DEFAULT_SOURCE_ORDER = [
    SourceName.DEPMAP,
    SourceName.PHAROS,
    SourceName.OPENTARGETS,
    SourceName.LITERATURE,
]


def _source_requested(query: CollectorRequest, source: SourceName) -> bool:
    requested = {item.value if hasattr(item, "value") else str(item) for item in query.sources}
    return source.value in requested


def selected_sources_for_request(query: CollectorRequest) -> list[SourceName]:
    return [source for source in DEFAULT_SOURCE_ORDER if _source_requested(query, source)]


def _build_memory_context(past_runs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    if not past_runs:
        return (
            {
                "match_count": 0,
                "latest_run_id": None,
                "latest_review_decision": None,
                "latest_evidence_count": 0,
            },
            ["No episodic memory match found; using default Phase-1 plan."],
        )

    latest = past_runs[-1]
    latest_review = latest.get("review_decision") or {}
    latest_decision = latest_review.get("decision")
    notes = [
        f"Found {len(past_runs)} prior episodic match(es) for this query.",
        (
            f"Latest related run `{latest.get('run_id')}` ended with decision `{latest_decision}`."
            if latest_decision
            else f"Latest related run `{latest.get('run_id')}` has no recorded review decision."
        ),
    ]
    if latest_decision == "approved":
        notes.append("Prior approved dossier exists; compare fresh evidence against that baseline.")
    elif latest_decision == "rejected":
        notes.append("Prior run was rejected; keep broad coverage and inspect prior failure context.")
    elif latest_decision == "needs_more_evidence":
        notes.append("Prior run requested more evidence; keep broad source coverage for recollection.")

    return (
        {
            "match_count": len(past_runs),
            "latest_run_id": latest.get("run_id"),
            "latest_review_decision": latest_decision,
            "latest_evidence_count": latest.get("evidence_count", 0),
            "recent_run_ids": [entry.get("run_id") for entry in past_runs[-3:]],
        },
        notes,
    )


def _planner_cache_enabled() -> bool:
    default = "0" if os.getenv("PYTEST_CURRENT_TEST") else "1"
    return os.getenv("A4T_PLANNER_CACHE_ENABLED", default).strip().lower() not in {"0", "false", "no"}


def _planner_cache_ttl_s() -> float:
    raw = os.getenv("A4T_PLANNER_CACHE_TTL_S", "86400").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 86400.0


def _planner_cache_dir(artifacts_root: str | Path | None = None) -> Path:
    root = Path(
        artifacts_root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent.parent.parent.parent / "artifacts"
    )
    path = root / "plans" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _planner_cache_key(query: CollectorRequest) -> str:
    # Cache key must be stable across run_id; only inputs that affect planning.
    payload = {
        "gene_symbol": query.gene_symbol,
        "disease_id": query.disease_id,
        "objective": query.objective,
        "species": query.species,
        "sources": [s.value if hasattr(s, "value") else str(s) for s in query.sources],
        "per_source_top_k": query.per_source_top_k,
        "max_literature_articles": query.max_literature_articles,
        "model_override": query.model_override,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:24]


def _load_cached_planning_response(query: CollectorRequest, *, artifacts_root: str | Path | None = None) -> PlanningResponse | None:
    if not _planner_cache_enabled():
        return None
    ttl_s = _planner_cache_ttl_s()
    if ttl_s <= 0:
        return None
    cache_path = _planner_cache_dir(artifacts_root) / f"{_planner_cache_key(query)}.planning_response.json"
    if not cache_path.exists():
        return None
    age_s = time.time() - cache_path.stat().st_mtime
    if age_s > ttl_s:
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        # Backward-compat: older cache entries stored `source_directives` as a dict[str,str].
        if isinstance(data, dict) and isinstance(data.get("source_directives"), dict):
            directives = data.get("source_directives") or {}
            data["source_directives"] = [
                {"source": key, "directive": value} for key, value in directives.items() if key and value
            ]
        return PlanningResponse.model_validate(data)
    except Exception:
        return None


def _persist_cached_planning_response(query: CollectorRequest, response: PlanningResponse, *, artifacts_root: str | Path | None = None) -> None:
    if not _planner_cache_enabled():
        return
    cache_path = _planner_cache_dir(artifacts_root) / f"{_planner_cache_key(query)}.planning_response.json"
    cache_path.write_text(
        json.dumps(response.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def build_collection_plan(
    query: CollectorRequest,
    *,
    past_runs: list[dict[str, Any]] | None = None,
) -> CollectionPlan:
    selected_sources = selected_sources_for_request(query)
    expected_outputs = {
        source.value: ["evidence_records", "source_status"]
        for source in selected_sources
    }
    memory_context, execution_notes = _build_memory_context(list(past_runs or []))

    query_variants: list[str] = [query.gene_symbol]
    for alias in sorted(gene_aliases(query.gene_symbol)):
        if alias not in query_variants:
            query_variants.append(alias)
    if query.disease_id:
        query_variants.append(f"{query.gene_symbol}:{query.disease_id}")

    cached = _load_cached_planning_response(query)
    if cached is not None:
        # Cache is keyed only on query inputs; it can become stale w.r.t episodic memory context.
        # Keep the cached strategic choices, but refresh the human-facing intent/notes to reflect
        # the current memory context for this run.
        memory_aware = PlanningAgent()._default_response(
            request=query,
            selected_sources=selected_sources,
            memory_context=memory_context,
            execution_notes=execution_notes,
            query_variants=query_variants,
        )
        cached.query_intent = memory_aware.query_intent
        cached.execution_notes = memory_aware.execution_notes
        planning_response, planning_mode, planner_model_used = cached, "cached_planner", None
    else:
        planning_response, planning_mode, planner_model_used = await PlanningAgent().plan(
            request=query,
            selected_sources=selected_sources,
            memory_context=memory_context,
            execution_notes=execution_notes,
            query_variants=query_variants,
        )
        _persist_cached_planning_response(query, planning_response)

    ordered_sources = [SourceName(source_name) for source_name in planning_response.source_order]
    return CollectionPlan(
        run_id=query.run_id,
        selected_sources=ordered_sources,
        query_intent=planning_response.query_intent,
        query_variants=planning_response.query_variants,
        memory_context=memory_context,
        execution_notes=planning_response.execution_notes,
        source_directives=planning_response.directives_dict(),
        retry_policy={
            "max_attempts": 3,
            "base_delay_ms": 100,
            "max_delay_ms": 400,
            "strategy": "bounded_exponential_backoff",
            "fallback": "emit_partial_result",
            "retryable_error_codes": ["timeout", "rate_limit", "upstream_error"],
        },
        expected_outputs=expected_outputs,
        planning_mode=planning_mode,
        planner_model_used=planner_model_used,
    )


def persist_collection_plan(
    plan: CollectionPlan,
    artifacts_root: str | Path | None = None,
) -> CollectionPlan:
    root = Path(
        artifacts_root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent.parent.parent.parent / "artifacts"
    )
    plan_dir = root / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{plan.run_id}.collection_plan.json"

    plan_with_path = plan.model_copy(update={"artifact_path": str(plan_path)})
    plan_path.write_text(
        json.dumps(plan_with_path.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return plan_with_path
