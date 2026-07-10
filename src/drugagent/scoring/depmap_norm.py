from __future__ import annotations

from typing import Optional


DEPMAP_CLIP_MIN = -2.0
DEPMAP_CLIP_MAX = 0.0


def normalize_depmap_ceres(ceres: float | int | None) -> Optional[float]:
    if ceres is None:
        return None
    value = float(ceres)
    ceres_clipped = max(DEPMAP_CLIP_MIN, min(DEPMAP_CLIP_MAX, value))
    # Formula: (0 - ceres_clipped) / (0 - DEPMAP_CLIP_MIN)
    normalized = (0.0 - ceres_clipped) / (0.0 - DEPMAP_CLIP_MIN)
    return normalized
