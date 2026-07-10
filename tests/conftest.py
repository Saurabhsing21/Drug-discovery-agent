from __future__ import annotations

import os

import pytest

# ── MUST RUN BEFORE ANY TEST MODULE IS IMPORTED ───────────────────────────────
# test_saved_runs_api.py imports `from ui_api.app import app` at module level.
# When pytest collects that file, ui_api/app.py executes its top-level
# os.environ.setdefault("A4T_REQUIRE_LLM_AGENTS", "1") call.
# Setting safe values here (conftest.py loads first) ensures setdefault is a
# no-op — the key is already present with the test-safe value "0".
os.environ.setdefault("A4T_REQUIRE_LLM_AGENTS", "0")
os.environ.setdefault("A4T_LLM_CALLS_ENABLED", "0")
os.environ.setdefault("A4T_LLM_FALLBACK_ENABLED", "1")
os.environ.setdefault("A4T_LLM_CROSS_PROVIDER_FALLBACK", "0")
os.environ.setdefault("A4T_REQUIRE_REVIEW", "0")
os.environ.setdefault("A4T_REQUIRE_PLAN_APPROVAL", "0")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore safe env-var defaults before every test.

    Handles tests that explicitly mutate os.environ so the next test
    doesn't inherit the mutated value.
    """
    monkeypatch.setenv("A4T_REQUIRE_LLM_AGENTS", "0")
    monkeypatch.setenv("A4T_LLM_CALLS_ENABLED", "0")
    monkeypatch.setenv("A4T_LLM_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("A4T_REQUIRE_REVIEW", "0")
    monkeypatch.setenv("A4T_REQUIRE_PLAN_APPROVAL", "0")
    yield


@pytest.fixture(autouse=True)
def _isolate_artifact_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Redirect artifact writes to a per-test temp directory.

    Tests that don't set A4T_ARTIFACT_DIR write to the real artifacts/ dir,
    polluting subsequent tests.  Setting a default tmp_path here gives each
    test a clean slate.  Tests that call monkeypatch.setenv("A4T_ARTIFACT_DIR",
    ...) after this fixture runs will override this value — that's fine.
    """
    monkeypatch.setenv("A4T_ARTIFACT_DIR", str(tmp_path))
    yield




@pytest.fixture(autouse=True)
def _reset_langgraph_checkpointer() -> None:
    """Clear the shared LangGraph MemorySaver between tests.

    agents/graph.py keeps COLLECTOR_CHECKPOINTER as a module-level singleton.
    Tests that run the full graph write checkpoints into it; the next test that
    reuses the same thread_id finds stale state and fails.  Clearing the
    underlying defaultdict before every test gives each test a clean slate.
    """
    try:
        import agents.graph as _graph_mod  # noqa: PLC0415
        _graph_mod.COLLECTOR_CHECKPOINTER.storage.clear()
    except Exception:
        pass
    yield
    try:
        import agents.graph as _graph_mod  # noqa: PLC0415
        _graph_mod.COLLECTOR_CHECKPOINTER.storage.clear()
    except Exception:
        pass

