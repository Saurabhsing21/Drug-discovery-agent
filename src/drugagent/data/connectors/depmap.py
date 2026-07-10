from __future__ import annotations

import os
import re
import time
import sys

from drugagent.core.schema import CollectorRequest, EvidenceRecord, Provenance, SourceName
from drugagent.scoring.depmap_norm import normalize_depmap_ceres

from .base import CollectorConnector

# ─────────────────────────────────────────────────────────────────────────────
# Cache path (populated by `python3 scripts/download_depmap.py`)
# ─────────────────────────────────────────────────────────────────────────────
_CACHE_DIR = os.getenv(
    "DEPMAP_CACHE_DIR",
    os.path.join(os.path.dirname(__file__), ".depmap_cache"),
)
_CACHE_FILE = os.path.join(_CACHE_DIR, "CRISPRGeneEffect.csv")

# Module-level singleton — loaded once, reused for every subsequent gene query
_df = None          # pandas DataFrame | None, columns = gene symbols, rows = cell lines
_col_map: dict[str, str] = {}   # normalized symbol → original column name


def _load_dataframe():
    """Load the CSV into memory once. Subsequent calls reuse the global."""
    global _df, _col_map

    if _df is not None:
        return  # Already loaded in this process

    import pandas as pd  # lazy import — only needed when cache exists
    print("[DepMap] Loading CRISPRGeneEffect.csv into memory… (one-time per session)", file=sys.stderr)
    _df = pd.read_csv(_CACHE_FILE, index_col=0)

    # Build symbol→column map: "BRAF (673)" → "BRAF"
    _col_map = {}
    for col in _df.columns:
        m = re.match(r"^\s*([A-Za-z0-9\-_.]+)", col)
        sym = m.group(1).upper() if m else col.strip().upper()
        _col_map[sym] = col

    print(
        f"[DepMap] Loaded {len(_df)} cell lines × {len(_df.columns)} genes into memory ✅",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Connector
# ─────────────────────────────────────────────────────────────────────────────
class DepMapConnector(CollectorConnector):
    source = SourceName.DEPMAP

    def __init__(self) -> None:
        self.files_api_url = os.getenv(
            "DEPMAP_FILES_API_URL",
            "https://depmap.org/portal/api/download/files",
        )

    async def collect(self, request: CollectorRequest):
        started_at = time.perf_counter()

        try:
            # 1. Require the local cache — skip if not present
            if not os.path.exists(_CACHE_FILE):
                message = (
                    "DepMap dataset not found locally. "
                    "Run `python3 scripts/download_depmap.py` once to download it (~300 MB). "
                    f"Expected path: {_CACHE_FILE}"
                )
                return [], self.skipped_status(started_at, message), []

            # 2. Load CSV into memory (no-op on subsequent calls within same process)
            _load_dataframe()
            if _df is None:
                raise RuntimeError("DepMap dataframe failed to load.")

            # 3. Find this gene's column
            symbol_norm = request.gene_symbol.strip().upper()
            col_name = _col_map.get(symbol_norm)

            if col_name is None:
                message = f"Gene '{request.gene_symbol}' not found in DepMap CRISPR dataset"
                return [], self.skipped_status(started_at, message), []

            # 4. Compute metrics using pandas — very fast, pure in-memory
            series = _df[col_name].dropna()
            if series.empty:
                message = f"No cell-line data for '{request.gene_symbol}' in DepMap"
                return [], self.skipped_status(started_at, message), []

            count = int(series.count())
            avg_effect = float(series.mean())
            median_effect = float(series.median())
            strong_dep = int((series <= -0.5).sum())
            strong_fraction = strong_dep / count
            normalized = normalize_depmap_ceres(avg_effect)
            normalized = 0.0 if normalized is None else max(0.0, min(1.0, normalized))
            confidence = min(0.95, 0.5 + min(count / 1000, 0.3) + min(strong_fraction * 0.3, 0.15))

            records: list[EvidenceRecord] = []

            # Record 1: aggregate dependency profile.
            aggregate_record = EvidenceRecord(
                source=self.source,
                target_id=request.gene_symbol,
                target_symbol=request.gene_symbol,
                disease_id=request.disease_id,
                evidence_type="genetic_dependency",
                raw_value=avg_effect,
                normalized_score=self.safe_float(normalized),
                confidence=self.safe_float(confidence),
                support={
                    "cell_line_count": count,
                    "average_gene_effect": round(avg_effect, 4),
                    "median_gene_effect": round(median_effect, 4),
                    "strong_dependency_count": strong_dep,
                    "strong_dependency_fraction": round(strong_fraction, 4),
                    "column_name": col_name,
                    "screen_type": "CRISPRGeneEffect",
                    "data_release": "DepMap 25Q3",
                },
                summary=(
                    f"DepMap CRISPR gene effect for {request.gene_symbol}: "
                    f"{avg_effect:.3f} avg across {count} cell lines "
                    f"({strong_dep} show strong dependency ≤ −0.5)."
                ),
                provenance=Provenance(
                    provider="DepMap (Broad Institute)",
                    endpoint=_CACHE_FILE,
                    query={"gene_symbol": request.gene_symbol},
                ),
            )
            records.append(aggregate_record)

            # Records 2..K: strongest cell-line dependencies for richer evidence depth.
            top_k = max(1, int(request.per_source_top_k))
            per_line_slots = max(0, top_k - 1)
            if per_line_slots > 0:
                strongest = series.nsmallest(per_line_slots)
                for idx, (cell_line_id, effect) in enumerate(strongest.items(), start=1):
                    effect_value = float(effect)
                    per_line_norm = normalize_depmap_ceres(effect_value)
                    per_line_norm = 0.0 if per_line_norm is None else max(0.0, min(1.0, per_line_norm))
                    per_line_conf = min(0.95, 0.55 + min(count / 1000, 0.25) + max(0.0, (0.7 - effect_value) * 0.05))
                    records.append(
                        EvidenceRecord(
                            source=self.source,
                            target_id=f"{request.gene_symbol}:{cell_line_id}",
                            target_symbol=request.gene_symbol,
                            disease_id=request.disease_id,
                            evidence_type="genetic_dependency_cell_line",
                            raw_value=effect_value,
                            normalized_score=self.safe_float(per_line_norm),
                            confidence=self.safe_float(per_line_conf),
                            support={
                                "cell_line_id": str(cell_line_id),
                                "gene_effect": effect_value,
                                "rank_within_gene": idx,
                                "column_name": col_name,
                                "screen_type": "CRISPRGeneEffect",
                                "data_release": "DepMap 25Q3",
                            },
                            summary=(
                                f"Cell-line dependency for {request.gene_symbol} in {cell_line_id}: "
                                f"gene_effect={effect_value:.3f} (rank {idx})."
                            ),
                            provenance=Provenance(
                                provider="DepMap (Broad Institute)",
                                endpoint=_CACHE_FILE,
                                query={"gene_symbol": request.gene_symbol, "cell_line_id": str(cell_line_id)},
                            ),
                        )
                    )

            return records, self.success_status(started_at, len(records)), []

        except Exception as exc:  # noqa: BLE001
            code = self.upstream_error_code(exc)
            message = f"DepMap processing failed: {exc}"
            return [], self.error_status(started_at, code, message), [
                self.error_record(code, message, retryable=False)
            ]
