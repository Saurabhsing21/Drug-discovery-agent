"""Saved runs CRUD router."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from api.saved_runs import SavedRunsUnavailable, delete_saved_run, get_saved_run, list_saved_runs, rename_saved_run, upsert_saved_run_from_snapshot
from api.dependencies import saved_run_payload
from api.models import RenameSavedRunInput, SaveRunInput, SaveRunResponse

router = APIRouter()


@router.get("/api/saved-runs")
async def list_saved_runs_route() -> dict[str, Any]:
    try:
        return {"saved_runs": await run_in_threadpool(list_saved_runs)}
    except SavedRunsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/saved-runs", response_model=SaveRunResponse)
async def save_run_route(body: SaveRunInput) -> SaveRunResponse:
    payload = saved_run_payload(body.run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Run not found or not ready to save")
    if body.title:
        payload["title"] = body.title.strip()
    try:
        saved_id = await run_in_threadpool(upsert_saved_run_from_snapshot, payload)
    except SavedRunsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SaveRunResponse(id=str(saved_id), run_id=str(payload["run_id"]), title=str(payload["title"]))


@router.get("/api/saved-runs/{saved_id}")
async def get_saved_run_route(saved_id: str) -> dict[str, Any]:
    try:
        item = await run_in_threadpool(get_saved_run, saved_id)
    except SavedRunsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Saved run not found")
    return item


@router.patch("/api/saved-runs/{saved_id}")
async def rename_saved_run_route(saved_id: str, body: RenameSavedRunInput) -> dict[str, Any]:
    try:
        item = await run_in_threadpool(rename_saved_run, saved_id, body.title)
    except SavedRunsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Saved run not found")
    return item


@router.delete("/api/saved-runs/{saved_id}")
async def delete_saved_run_route(saved_id: str) -> dict[str, Any]:
    try:
        ok = await run_in_threadpool(delete_saved_run, saved_id)
    except SavedRunsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Saved run not found")
    return {"id": saved_id, "status": "deleted"}
