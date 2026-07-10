"""Run state store — migrated from agents/run_state_store.py."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import TypeAlias


def _artifact_root() -> Path:
    return Path(os.getenv("A4T_ARTIFACT_DIR") or Path(__file__).resolve().parent.parent / "artifacts")


def _working_memory_dir(run_id: str) -> Path:
    return _artifact_root() / "working_memory" / run_id


def _to_jsonable(value: Any) -> Any:
    # LangGraph uses wrapper types (e.g. Overwrite) in state updates; unwrap them for persistence.
    OverwriteType: TypeAlias = type[Any]
    overwrite_cls: OverwriteType | None
    try:
        from langgraph.types import Overwrite as _Overwrite
        overwrite_cls = _Overwrite
    except Exception:  # pragma: no cover
        overwrite_cls = None
    if overwrite_cls is not None and isinstance(value, overwrite_cls):
        return _to_jsonable(getattr(value, "value", None))
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class FallbackSnapshot:
    """A minimal snapshot compatible with the UI API's `get_state` response."""

    values: dict[str, Any]
    next: tuple[str, ...]


@dataclass(frozen=True)
class PersistedRunState:
    run_id: str
    status: str  # running|completed|paused|failed
    last_stage: str | None
    next: tuple[str, ...]
    values: dict[str, Any]
    updated_at_ms: int
    error: str | None = None

    def as_snapshot(self) -> FallbackSnapshot:
        return FallbackSnapshot(values=self.values, next=self.next)


class RunStateStore:
    """File-based persistence for UI state across API restarts.

    LangGraph's default MemorySaver is in-memory; this store provides a lightweight
    fallback for `/api/runs/{id}/state` by keeping the latest merged state snapshot
    in `artifacts/working_memory/<run_id>/latest.json`.
    """

    @staticmethod
    def latest_path(run_id: str) -> Path:
        return _working_memory_dir(run_id) / "latest.json"

    @staticmethod
    def write_latest(
        run_id: str,
        *,
        stage: str,
        state: dict[str, Any],
        update: dict[str, Any] | None = None,
        next_stages: tuple[str, ...] | None = None,
        status: str = "running",
        error: str | None = None,
    ) -> str:
        directory = _working_memory_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)

        merged: dict[str, Any] = {}
        merged.update(_to_jsonable(state))
        if update:
            merged.update(_to_jsonable(update))

        # Heuristic: mark completed if a final artifact payload is present.
        # Note: some stages intentionally set `final_result=None` while recollecting; do not treat that as completion.
        if status == "running" and (merged.get("final_result") is not None or merged.get("final_dossier") is not None):
            status = "completed"

        payload = {
            "run_id": run_id,
            "status": status,
            "last_stage": stage,
            "next": list(next_stages or ()),
            "values": merged,
            "updated_at_ms": int(time.time() * 1000),
            "error": error,
        }

        path = RunStateStore.latest_path(run_id)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return str(path)

    @staticmethod
    def load_latest(run_id: str) -> PersistedRunState | None:
        path = RunStateStore.latest_path(run_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

        values = raw.get("values") if isinstance(raw, dict) else None
        if not isinstance(values, dict):
            return None

        next_raw = raw.get("next") if isinstance(raw, dict) else None
        next_stages: tuple[str, ...] = tuple(str(s) for s in next_raw) if isinstance(next_raw, list) else ()

        return PersistedRunState(
            run_id=str(raw.get("run_id", run_id)) if isinstance(raw, dict) else run_id,
            status=str(raw.get("status", "running")) if isinstance(raw, dict) else "running",
            last_stage=str(raw.get("last_stage")) if isinstance(raw, dict) and raw.get("last_stage") else None,
            next=next_stages,
            values=values,
            updated_at_ms=int(raw.get("updated_at_ms", 0)) if isinstance(raw, dict) else 0,
            error=str(raw.get("error")) if isinstance(raw, dict) and raw.get("error") else None,
        )
