from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _working_memory_dir(run_id: str, root: str | Path | None = None) -> Path:
    base = Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )
    return base / "working_memory" / run_id


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def persist_working_memory_snapshot(run_id: str, stage: str, payload: dict[str, Any], root: str | Path | None = None) -> str:
    write_enabled = os.getenv("A4T_WORKING_MEMORY_WRITE_STAGE_SNAPSHOTS", "1").strip().lower() not in {"0", "false", "no"}
    directory = _working_memory_dir(run_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}.json"
    if write_enabled:
        path.write_text(json.dumps(_to_jsonable(payload), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return str(path)
