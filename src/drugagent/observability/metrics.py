from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from drugagent.core.schema import ConflictRecord, SourceStatus, StatusName, VerificationReport


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def build_run_metrics(
    *,
    run_id: str,
    source_status: list[SourceStatus],
    verification_report: VerificationReport,
    conflicts: list[ConflictRecord],
) -> dict[str, Any]:
    source_latency_ms = {
        (status.source.value if hasattr(status.source, "value") else str(status.source)): status.duration_ms
        for status in source_status
    }
    source_statuses = {
        (status.source.value if hasattr(status.source, "value") else str(status.source)): (
            status.status.value if hasattr(status.status, "value") else str(status.status)
        )
        for status in source_status
    }
    source_record_counts = {
        (status.source.value if hasattr(status.source, "value") else str(status.source)): status.record_count
        for status in source_status
    }
    success_count = sum(1 for status in source_status if status.status == StatusName.SUCCESS)
    failed_count = sum(1 for status in source_status if status.status == StatusName.FAILED)
    requested_sources = len(source_status)

    return {
        "run_id": run_id,
        "requested_source_count": requested_sources,
        "successful_source_count": success_count,
        "failed_source_count": failed_count,
        "success_ratio": (success_count / requested_sources) if requested_sources else 0.0,
        "source_latency_ms": source_latency_ms,
        "source_statuses": source_statuses,
        "source_record_counts": source_record_counts,
        "verification_fail_count": verification_report.fail_count,
        "verification_blocked": verification_report.blocked,
        "blocking_issue_count": verification_report.blocking_issue_count,
        "warning_issue_count": verification_report.warning_count,
        "conflict_count": len(conflicts),
        "conflict_rate": (len(conflicts) / requested_sources) if requested_sources else 0.0,
    }


def persist_run_metrics(
    metrics: dict[str, Any],
    root: str | Path | None = None,
) -> str:
    metrics_dir = _artifact_root(root) / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    path = metrics_dir / f"{metrics['run_id']}.metrics.json"
    path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
