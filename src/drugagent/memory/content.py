from __future__ import annotations

import os
from pathlib import Path


def content_memory_enabled() -> bool:
    return os.getenv("A4T_CONTENT_MEMORY_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def content_memory_path() -> Path | None:
    raw = os.getenv("A4T_CONTENT_MEMORY_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    # Prefer an explicit curated file, else fall back to the core "what we're building" doc.
    repo_root = Path(__file__).resolve().parent.parent
    preferred = repo_root / "docs" / "CONTENT_MEMORY.md"
    if preferred.exists():
        return preferred
    fallback = repo_root / "docs" / "WHAT_WE_ARE_BUILDING.md"
    return fallback if fallback.exists() else None


def _max_chars() -> int:
    raw = os.getenv("A4T_CONTENT_MEMORY_MAX_CHARS", "6000").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 6000


def load_content_memory() -> str:
    """Load a curated project context payload to inject into LLM prompts."""
    if not content_memory_enabled():
        return ""
    path = content_memory_path()
    if path is None or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    limit = _max_chars()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text.strip()
    return (text[:limit] + "\n\n[TRUNCATED]\n").strip()


def inject_content_memory(prompt: str) -> str:
    context = load_content_memory()
    if not context:
        return prompt
    return (
        "Project context (content memory):\n"
        + context
        + "\n\n"
        + prompt
    )

