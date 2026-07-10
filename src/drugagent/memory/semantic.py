from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_KNOWLEDGE = {
    "gene_aliases": {
        "EGFR": ["ERBB1"],
        "TP53": ["P53"],
        "KRAS": ["K-RAS"],
    },
    "disease_aliases": {},
}


def _semantic_memory_path(root: str | Path | None = None) -> Path:
    base = Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )
    return base / "semantic_memory" / "knowledge.json"


def load_semantic_memory(root: str | Path | None = None) -> dict:
    path = _semantic_memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_KNOWLEDGE, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def gene_aliases(symbol: str, root: str | Path | None = None) -> set[str]:
    knowledge = load_semantic_memory(root)
    aliases = set(knowledge.get("gene_aliases", {}).get(symbol.strip().upper(), []))
    aliases.add(symbol.strip().upper())
    return {alias.upper() for alias in aliases}


def disease_aliases(disease_id: str | None, root: str | Path | None = None) -> set[str]:
    if not disease_id:
        return set()
    canonical = disease_id.strip()
    aliases = {canonical}
    if ":" in canonical:
        aliases.add(canonical.replace(":", "_", 1))
    if "_" in canonical:
        aliases.add(canonical.replace("_", ":", 1))
    knowledge = load_semantic_memory(root)
    aliases.update(knowledge.get("disease_aliases", {}).get(canonical, []))
    return {alias for alias in aliases if alias}
