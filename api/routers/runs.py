"""Runs router: create, resume, cancel, state, events, artifacts."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from agents.graph import CollectionPaused, get_collection_state, resume_collection_graph, run_collection_graph
from agents.provider_select import current_provider_selection, select_provider_once
from agents.run_state_store import RunStateStore
from agents.schema import CollectorRequest, SourceName
from agents.artifact_store import artifact_layout, artifact_root
from agents.query_interpretation_agent import QueryInterpretationAgent, QueryInterpretationContext
from api.dependencies import RUN_TASKS
from api.event_bus import BUS
from api.models import (
    CreateRunInput,
    CreateRunResponse,
    CreateRunFromTextInput,
    ResumeRunResponse,
)

router = APIRouter()


async def _cancel_run(run_id: str, reason: str) -> None:
    task = RUN_TASKS.get(run_id)
    if not task or task.done():
        return
    task.cancel()
    persisted = RunStateStore.load_latest(run_id)
    if persisted is not None:
        RunStateStore.write_latest(
            run_id,
            stage=persisted.last_stage or "cancelled",
            state=persisted.values,
            update=None,
            next_stages=(),
            status="cancelled",
            error=reason,
        )
    BUS.publish(run_id, "run_cancelled", {"run_id": run_id, "status": "cancelled", "reason": reason})


async def _run_in_background(request: CollectorRequest, *, is_resume: bool = False) -> None:
    run_id = request.run_id
    BUS.ensure_run(run_id)

    def on_progress(event_type: str, payload: dict[str, Any]) -> None:
        BUS.publish(run_id, event_type, payload)

    try:
        if is_resume:
            BUS.publish(run_id, "run_status", {"run_id": run_id, "status": "resuming"})
            result = await resume_collection_graph(request, progress_cb=on_progress)
        else:
            BUS.publish(run_id, "run_status", {"run_id": run_id, "status": "running"})
            result = await run_collection_graph(request, progress_cb=on_progress)

        BUS.publish(run_id, "run_completed", {"run_id": run_id, "status": "completed", "result": result.model_dump(mode="json") if hasattr(result, "model_dump") else result})
    except asyncio.CancelledError:
        await _cancel_run(run_id, "cancelled_by_user")
        raise
    except CollectionPaused as exc:
        BUS.publish(run_id, "run_paused", {"run_id": run_id, "reason": exc.reason, "next_stages": list(exc.next_stages)})
    except Exception as exc:  # noqa: BLE001
        BUS.publish(run_id, "run_failed", {"run_id": run_id, "status": "failed", "error": str(exc)})


@router.post("/api/runs", response_model=CreateRunResponse)
async def create_run(body: CreateRunInput) -> CreateRunResponse:
    await select_provider_once()
    request = body.to_request()
    run_id = request.run_id
    BUS.ensure_run(run_id)
    await _cancel_run(run_id, "superseded_by_new_run")
    task = asyncio.create_task(_run_in_background(request, is_resume=False))
    RUN_TASKS[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: RUN_TASKS.pop(rid, None))
    return CreateRunResponse(run_id=run_id, status="started")


@router.post("/api/runs/from-text", response_model=CreateRunResponse)
async def create_run_from_text(body: CreateRunFromTextInput) -> CreateRunResponse:
    await select_provider_once()
    interp = QueryInterpretationAgent(model=body.model_override)
    parsed = await interp.interpret(message=body.message, context=QueryInterpretationContext(mode="new_run"))
    if not parsed.in_scope:
        raise HTTPException(status_code=400, detail=parsed.user_message_to_show_if_out_of_scope)
    if not parsed.gene_symbol:
        raise HTTPException(status_code=400, detail="Could not extract a target gene symbol from the message.")

    run_id = body.run_id or f"run-{__import__('uuid').uuid4().hex[:12]}"
    request = CollectorRequest(
        gene_symbol=parsed.gene_symbol,
        disease_id=parsed.disease_id,
        objective=parsed.objective,
        sources=body.sources or [SourceName.DEPMAP, SourceName.PHAROS, SourceName.OPENTARGETS, SourceName.LITERATURE],
        per_source_top_k=body.per_source_top_k,
        max_literature_articles=body.max_literature_articles,
        model_override=body.model_override,
        run_id=run_id,
    )
    BUS.ensure_run(run_id)
    await _cancel_run(run_id, "superseded_by_new_run")
    task = asyncio.create_task(_run_in_background(request, is_resume=False))
    RUN_TASKS[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: RUN_TASKS.pop(rid, None))
    return CreateRunResponse(run_id=run_id, status="started")


@router.post("/api/runs/{run_id}/resume", response_model=ResumeRunResponse)
async def resume_run(run_id: str) -> ResumeRunResponse:
    await select_provider_once()
    snapshot = await get_collection_state(run_id)
    request = snapshot.values.get("query")
    if request is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id `{run_id}`")
    if isinstance(request, dict):
        request = CollectorRequest.model_validate(request)
    await _cancel_run(run_id, "superseded_by_resume")
    task = asyncio.create_task(_run_in_background(request, is_resume=True))
    RUN_TASKS[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: RUN_TASKS.pop(rid, None))
    return ResumeRunResponse(run_id=run_id, status="resumed")


@router.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    await _cancel_run(run_id, "cancelled_by_user")
    return {"run_id": run_id, "status": "cancelled"}


@router.get("/api/runs/{run_id}/state")
async def get_state(run_id: str) -> dict[str, Any]:
    snapshot = await get_collection_state(run_id)
    persisted = RunStateStore.load_latest(run_id)
    if persisted is not None and persisted.status == "running" and run_id not in RUN_TASKS:
        RunStateStore.write_latest(
            run_id,
            stage=persisted.last_stage or "unknown",
            state=persisted.values,
            update=None,
            next_stages=(),
            status="cancelled",
            error="cancelled_or_server_restart",
        )
        persisted = RunStateStore.load_latest(run_id)
    return {
        "run_id": run_id,
        "next": list(snapshot.next),
        "values": jsonable_encoder(snapshot.values),
        "_runtime": (current_provider_selection() or (await select_provider_once())).as_dict(),
        "_persisted": (
            {"status": persisted.status, "last_stage": persisted.last_stage, "updated_at_ms": persisted.updated_at_ms, "error": persisted.error}
            if persisted is not None
            else None
        ),
    }


def _sse_encode(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    queue = BUS.subscribe(run_id)

    async def gen():
        yield _sse_encode("connected", {"run_id": run_id}).encode("utf-8")
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse_encode(item.event, {**item.data, "_ts_ms": item.created_at_ms}).encode("utf-8")
                except asyncio.TimeoutError:
                    yield _sse_encode("ping", {"run_id": run_id}).encode("utf-8")
        finally:
            BUS.unsubscribe(run_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/api/runs/{run_id}/artifacts")
async def get_artifacts(run_id: str) -> dict[str, Any]:
    import os
    layout = artifact_layout(run_id)
    root = artifact_root().resolve()
    artifacts: dict[str, Any] = {}
    for key, raw_path in layout.items():
        try:
            path = (root / os.path.relpath(raw_path, str(root))).resolve()
        except Exception:
            artifacts[key] = {"path": raw_path, "exists": False, "kind": "unknown"}
            continue
        if root not in path.parents and path != root:
            artifacts[key] = {"path": raw_path, "exists": False, "kind": "outside_root"}
            continue
        artifacts[key] = {"path": str(path), "exists": path.exists(), "kind": "dir" if path.exists() and path.is_dir() else "file"}
    epi = root / "episodic_memory" / "runs.json"
    artifacts["episodic_memory_runs"] = {"path": str(epi), "exists": epi.exists(), "kind": "file"}
    return {"run_id": run_id, "artifacts": artifacts}
