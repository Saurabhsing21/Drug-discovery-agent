from __future__ import annotations

import json
import os
from pathlib import Path

from drugagent.core.schema import EvidenceDossier


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def persist_evidence_dossier(
    dossier: EvidenceDossier,
    root: str | Path | None = None,
) -> str:
    dossier_dir = _artifact_root(root) / "dossiers"
    dossier_dir.mkdir(parents=True, exist_ok=True)
    path = dossier_dir / f"{dossier.run_id}.evidence_dossier.json"
    path.write_text(
        json.dumps(dossier.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)
