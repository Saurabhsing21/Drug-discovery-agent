from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from drugagent.core.schema import EvidenceDossier


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def _episodic_memory_path(root: str | Path | None = None) -> Path:
    base = _artifact_root(root)
    memory_dir = base / "episodic_memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir / "runs.json"


def _load_store(root: str | Path | None = None) -> list[dict[str, Any]]:
    path = _episodic_memory_path(root)
    if not path.exists():
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text("[]\n", encoding="utf-8")
        try:
            tmp.replace(path)
        except Exception:
            path.write_text("[]\n", encoding="utf-8")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Corrupt or partial writes should never crash the workflow; quarantine and reset.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            path.rename(path.with_suffix(f".json.corrupt.{stamp}"))
        except Exception:
            pass
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text("[]\n", encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass
        return []


def persist_episodic_memory(dossier: EvidenceDossier, root: str | Path | None = None) -> str:
    path = _episodic_memory_path(root)
    store = [entry for entry in _load_store(root) if entry.get("run_id") != dossier.run_id]
    created_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    store.append(
        {
            "run_id": dossier.run_id,
            "created_at_ms": created_at_ms,
            "gene_symbol": dossier.query.gene_symbol,
            "disease_id": dossier.query.disease_id,
            "review_decision": dossier.review_decision.model_dump(mode="json") if dossier.review_decision else None,
            "evidence_count": len(dossier.verified_evidence),
            "conflict_count": len(dossier.conflicts),
            "dossier_artifact_path": dossier.artifact_path,
            "summary_excerpt": dossier.summary_markdown.splitlines()[0] if dossier.summary_markdown else "",
        }
    )
    # Keep a stable chronological order so "latest" selection is meaningful for memory-aware planning.
    store_sorted = sorted(store, key=lambda entry: (int(entry.get("created_at_ms") or 0), str(entry.get("run_id") or "")))
    payload = json.dumps(store_sorted, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return str(path)


def get_episodic_memory_by_run_id(run_id: str, root: str | Path | None = None) -> dict[str, Any] | None:
    return next((entry for entry in _load_store(root) if entry.get("run_id") == run_id), None)


def query_episodic_memory(
    *,
    gene_symbol: str | None = None,
    disease_id: str | None = None,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    entries = _load_store(root)
    result = entries
    if gene_symbol:
        result = [entry for entry in result if entry.get("gene_symbol") == gene_symbol]
    if disease_id:
        result = [entry for entry in result if entry.get("disease_id") == disease_id]
    return sorted(result, key=lambda entry: (int(entry.get("created_at_ms") or 0), str(entry.get("run_id") or "")))
