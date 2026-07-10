from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from drugagent.core.schema import CollectorRequest, SourceName


@dataclass
class HealthCheckResult:
    source: str
    healthy: bool
    message: str


def run_source_health_checks(request: CollectorRequest) -> list[HealthCheckResult]:
    results: list[HealthCheckResult] = []
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for source in request.sources:
        source_name = source.value if hasattr(source, "value") else str(source)
        if source_name == SourceName.DEPMAP.value:
            cache_file = os.getenv(
                "DEPMAP_CACHE_FILE",
                os.path.join(project_root, "mcps", "connectors", ".depmap_cache", "CRISPRGeneEffect.csv"),
            )
            healthy = os.path.exists(cache_file)
            message = "DepMap cache available." if healthy else f"DepMap cache missing at {cache_file}"
            results.append(HealthCheckResult(source=source_name, healthy=healthy, message=message))
        elif source_name == SourceName.PHAROS.value:
            results.append(HealthCheckResult(source=source_name, healthy=True, message="PHAROS GraphQL available."))
        elif source_name == SourceName.EXT_PHAROS.value:
            script = os.path.join(project_root, "external_mcps", "run_pharos_mcp.sh")
            healthy = os.path.exists(script)
            message = "External Pharos launcher available." if healthy else f"External Pharos launcher missing at {script}"
            results.append(HealthCheckResult(source=source_name, healthy=healthy, message=message))
        else:
            results.append(HealthCheckResult(source=source_name, healthy=True, message="Source configuration available."))

    return results


def validate_source_health(request: CollectorRequest) -> None:
    failures = [
        result
        for result in run_source_health_checks(request)
        if not result.healthy and result.source == SourceName.EXT_PHAROS.value
    ]
    if failures:
        raise RuntimeError("; ".join(result.message for result in failures))


def persist_health_report(run_id: str, results: list[HealthCheckResult], root: str | Path | None = None) -> str:
    base = Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )
    report_dir = base / "health_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{run_id}.health.json"
    payload = [
        {"source": result.source, "healthy": result.healthy, "message": result.message}
        for result in results
    ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
