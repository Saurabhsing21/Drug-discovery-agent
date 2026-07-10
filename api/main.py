"""FastAPI application factory.

Canonical entry point: ``uvicorn api.main:app``
The legacy ``ui_api/app.py`` shims to this module.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import artifacts, compare, followup, health, review, runs, saved_runs
from api.saved_comparisons import router as saved_comparisons_router


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


# ── Load .env before any env-var reads ────────────────────────────────────────
try:
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _key in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.getenv(_key, None) == "":
            os.environ.pop(_key, None)
    load_dotenv(os.path.join(_repo_root, ".env"), override=False)
except Exception:
    pass

# ── Provider defaults ──────────────────────────────────────────────────────────
if not os.getenv("A4T_LLM_PROVIDER"):
    if os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip():
        os.environ.setdefault("A4T_LLM_PROVIDER", "google")

os.environ.setdefault("A4T_REQUIRE_LLM_AGENTS", "1")
os.environ.setdefault("A4T_LLM_CALLS_ENABLED", "1")
os.environ.setdefault("A4T_LLM_FALLBACK_ENABLED", "1")
os.environ.setdefault("A4T_LLM_CROSS_PROVIDER_FALLBACK", "1")

if not os.getenv("A4T_REPORT_FORMAT"):
    os.environ.setdefault("A4T_REPORT_FORMAT", "compiler")

if not _bool_env("A4T_LLM_CALLS_ENABLED", "1"):
    os.environ["A4T_REQUIRE_LLM_AGENTS"] = "0"
    os.environ.setdefault("A4T_LLM_FALLBACK_ENABLED", "1")
    os.environ.setdefault("A4T_REPORT_FORMAT", "structured")

os.environ.setdefault("A4T_REQUIRE_REVIEW", "0")
os.environ.setdefault("A4T_REQUIRE_PLAN_APPROVAL", "0")
os.environ.setdefault("A4T_LLM_CONCURRENCY", "1")
os.environ.setdefault("A4T_LLM_MIN_INTERVAL_S", "3.0")
os.environ.setdefault("A4T_LLM_RETRY_ATTEMPTS", "3")
os.environ.setdefault("A4T_LLM_TIMEOUT_S", "300")
os.environ.setdefault("A4T_LLM_429_BASE_DELAY_S", "2.0")
os.environ.setdefault("A4T_LLM_429_MAX_DELAY_S", "8.0")
os.environ.setdefault("A4T_LLM_RPM", "10")
os.environ.setdefault("A4T_SOURCE_DISPATCH_MODE", "sequential")

# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(title="Drugagent UI Gateway", version="0.1.0")

app.include_router(saved_comparisons_router, prefix="/api")
app.include_router(health.router)
app.include_router(runs.router)
app.include_router(review.router)
app.include_router(followup.router)
app.include_router(compare.router)
app.include_router(artifacts.router)
app.include_router(saved_runs.router)

if _bool_env("A4T_UI_CORS_ENABLED", "1"):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.getenv("A4T_UI_CORS_ORIGINS", "http://localhost:3000").split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
async def _startup() -> None:
    from agents.provider_select import select_provider_once
    from api.db import init_db
    try:
        await select_provider_once()
    except Exception:
        pass
    try:
        init_db()
    except Exception:
        pass
