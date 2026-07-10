"""Shared app state and helper utilities for all routers."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from agents.run_state_store import RunStateStore

# Shared in-flight task registry
RUN_TASKS: dict[str, asyncio.Task] = {}


def bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def default_title_from_values(values: dict[str, Any]) -> str:
    query = values.get("query") if isinstance(values.get("query"), dict) else {}
    objective = str(query.get("objective") or "").strip()
    if objective:
        return objective
    gene = str(query.get("gene_symbol") or "").strip()
    if gene:
        return f"Research: {gene}"
    return "Saved run"


def saved_run_payload(run_id: str) -> dict[str, Any] | None:
    persisted = RunStateStore.load_latest(run_id)
    if persisted is None:
        return None
    values = persisted.values or {}
    query = values.get("query") if isinstance(values.get("query"), dict) else {}
    final_dossier = values.get("final_dossier") if isinstance(values.get("final_dossier"), dict) else None
    summary = None
    if final_dossier and isinstance(final_dossier.get("summary_markdown"), str):
        summary = final_dossier.get("summary_markdown")
    if summary is None:
        summary = values.get("explanation") if isinstance(values.get("explanation"), str) else None
    return {
        "run_id": run_id,
        "title": default_title_from_values(values),
        "gene_symbol": str(query.get("gene_symbol") or "").strip() or None,
        "disease_id": str(query.get("disease_id") or "").strip() or None,
        "objective": str(query.get("objective") or "").strip() or None,
        "summary_markdown": summary,
        "scored_target": values.get("scored_target") if isinstance(values.get("scored_target"), dict) else None,
        "final_dossier": final_dossier,
        "evidence_graph": values.get("evidence_graph") if isinstance(values.get("evidence_graph"), dict) else None,
    }
