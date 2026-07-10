from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from drugagent.memory.working import persist_working_memory_snapshot


def prompt_tracing_enabled() -> bool:
    return os.getenv("A4T_PROMPT_TRACE_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


def _trace_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def persist_prompt_trace(
    *,
    run_id: str,
    agent_name: str,
    stage_name: str,
    model: str | None,
    provider: str | None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    extra: dict[str, Any] | None = None,
    root: str | Path | None = None,
) -> dict[str, str]:
    """Persist prompts used for a run without leaking secrets.

    Writes:
    - artifacts/prompts/<run_id>/<agent_name>.<stage>.system.txt (optional)
    - artifacts/prompts/<run_id>/<agent_name>.<stage>.user.txt (optional)
    Also records an index entry in working memory for traceability.
    """
    if not prompt_tracing_enabled():
        return {}

    base = _trace_root(root) / "prompts" / run_id
    base.mkdir(parents=True, exist_ok=True)

    def _safe_name(value: str) -> str:
        return "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_", "."})[:80]

    key = f"{_safe_name(agent_name)}.{_safe_name(stage_name)}"
    out: dict[str, str] = {}
    if system_prompt is not None:
        path = base / f"{key}.system.txt"
        path.write_text(system_prompt, encoding="utf-8")
        out["system_prompt_path"] = str(path)
    if user_prompt is not None:
        path = base / f"{key}.user.txt"
        path.write_text(user_prompt, encoding="utf-8")
        out["user_prompt_path"] = str(path)

    payload = {
        "agent_name": agent_name,
        "stage_name": stage_name,
        "model": model,
        "provider": provider,
        "prompt_paths": out,
        "extra": extra or {},
    }
    persist_working_memory_snapshot(run_id, f"prompt_trace.{agent_name}.{stage_name}", payload)
    return out

