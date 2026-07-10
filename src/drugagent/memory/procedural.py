from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from drugagent.llm import prompts
from drugagent.core.schema import NORMALIZATION_POLICY_VERSION, PLAN_VERSION, SCHEMA_VERSION, VERIFICATION_POLICY_VERSION


def _procedural_memory_dir(root: str | Path | None = None) -> Path:
    base = Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )
    return base / "procedural_memory"


def procedural_memory_payload(run_id: str, collector_node_sequence: list[str]) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(prompts.get_system_prompt().encode("utf-8")).hexdigest()[:12]
    return {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "plan_version": PLAN_VERSION,
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "verification_policy_version": VERIFICATION_POLICY_VERSION,
        "summary_prompt_hash": prompt_hash,
        "collector_node_sequence": list(collector_node_sequence),
    }


def persist_procedural_memory(run_id: str, collector_node_sequence: list[str], root: str | Path | None = None) -> str:
    directory = _procedural_memory_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.procedural_memory.json"
    payload = procedural_memory_payload(run_id, collector_node_sequence)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
