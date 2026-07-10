from __future__ import annotations

import os
from pathlib import Path


ARTIFACT_DIRS = [
    "plans",
    "graphs",
    "dossiers",
    "evidence_dashboards",
    "metrics",
    "health_reports",
    "review_audit",
    "review_decisions",
    "episodic_memory",
    "procedural_memory",
    "working_memory",
]


def artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def artifact_layout(run_id: str, root: str | Path | None = None) -> dict[str, str]:
    base = artifact_root(root)
    return {
        "plan": str(base / "plans" / f"{run_id}.collection_plan.json"),
        "graph": str(base / "graphs" / f"{run_id}.evidence_graph.json"),
        "dossier": str(base / "dossiers" / f"{run_id}.evidence_dossier.json"),
        "evidence_dashboard": str(base / "evidence_dashboards" / f"{run_id}.evidence_dashboard.html"),
        "metrics": str(base / "metrics" / f"{run_id}.metrics.json"),
        "health_report": str(base / "health_reports" / f"{run_id}.health.json"),
        "review_audit": str(base / "review_audit" / run_id),
        "review_decision": str(base / "review_decisions" / f"{run_id}.review_decision.json"),
        "procedural_memory": str(base / "procedural_memory" / f"{run_id}.procedural_memory.json"),
        "working_memory": str(base / "working_memory" / run_id),
    }


def apply_retention_policy(
    *,
    root: str | Path | None = None,
    retain_run_ids: set[str] | None = None,
) -> list[str]:
    retain_run_ids = retain_run_ids or set()
    base = artifact_root(root)
    deleted: list[str] = []

    for dirname in ARTIFACT_DIRS:
        directory = base / dirname
        if not directory.exists():
            continue
        for path in directory.iterdir():
            run_prefix = path.name.split(".", 1)[0]
            if path.is_dir():
                run_prefix = path.name
            if run_prefix in retain_run_ids or dirname == "episodic_memory":
                continue
            if path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file():
                        child.unlink()
                for child in sorted(path.rglob("*"), reverse=True):
                    if child.is_dir():
                        child.rmdir()
                path.rmdir()
            else:
                path.unlink()
            deleted.append(str(path))

    return sorted(deleted)
