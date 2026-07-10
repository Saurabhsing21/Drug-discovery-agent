"""Health check router."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from agents.provider_select import current_provider_selection, select_provider_once

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": (current_provider_selection() or (await select_provider_once())).as_dict(),
        "report_format": os.getenv("A4T_REPORT_FORMAT", ""),
        "llm_calls_enabled": os.getenv("A4T_LLM_CALLS_ENABLED", ""),
        "require_llm_agents": os.getenv("A4T_REQUIRE_LLM_AGENTS", ""),
        "llm_timeout_s": os.getenv("A4T_LLM_TIMEOUT_S", ""),
        "require_llm_planner": os.getenv("A4T_REQUIRE_LLM_PLANNER", "0"),
        "has_google_key": bool(os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()),
    }
