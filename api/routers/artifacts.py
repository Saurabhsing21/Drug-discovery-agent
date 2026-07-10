"""Artifacts (evidence dashboard) router."""
from __future__ import annotations
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from agents.artifact_store import artifact_layout, artifact_root
from agents.visualize_evidence import generate_evidence_html
from agents.run_state_store import RunStateStore

router = APIRouter()


@router.get("/api/runs/{run_id}/evidence-dashboard")
async def get_evidence_dashboard(run_id: str) -> Response:
    layout = artifact_layout(run_id)
    root = artifact_root().resolve()
    raw_path = layout.get("evidence_dashboard")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Evidence dashboard path is not configured.")
    latest_path = (root / "working_memory" / run_id / "latest.json").resolve()
    if latest_path.exists():
        try:
            latest_state = json.loads(latest_path.read_text())
            scored = (latest_state.get("values") or {}).get("scored_target")
            if scored:
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
                generate_evidence_html([scored], raw_path)
        except Exception:
            pass
    try:
        path = (root / os.path.relpath(raw_path, str(root))).resolve()
    except Exception:
        raise HTTPException(status_code=404, detail="Evidence dashboard not found.")
    if root not in path.parents and path != root:
        raise HTTPException(status_code=404, detail="Evidence dashboard not found.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Evidence dashboard not found (not generated yet).")
    return Response(path.read_text(encoding="utf-8"), media_type="text/html")
