from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class SourceEvidence(BaseModel):
    """Input evidence contract for the scoring agent."""
    source: str                    # 'pharos' | 'depmap' | 'open_targets' | 'literature'
    gene: str                      # gene symbol e.g. 'EGFR'
    data_present: bool             # did this source return anything at all?
    total_available: int           # total records in source for this gene (pre top-k)
    top_k_results: list[dict[str, Any]] = Field(default_factory=list)
    # NOTE: Some sources have categorical signals (e.g., PHAROS TDL), so this is not strictly numeric.
    raw_signal: float | int | str | None = None
    raw_signal_label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoredTarget(BaseModel):
    """Output contract emitted by the scoring agent."""
    gene: str

    # Primary outputs
    target_score: float            # 0.0–1.0 weighted aggregate
    evidence_confidence: float     # 0.0–1.0 how much data existed

    # Per-source breakdown
    source_scores: dict[str, float]          # normalized 0-1 score per source
    source_confidences: dict[str, str]       # 'high'|'medium'|'low'|'missing'
    weights_used: dict[str, float]           # actual weights after rebalancing

    # Flags
    conflict_flag: bool = False
    conflict_detail: Optional[str] = None
    missing_sources: list[str] = Field(default_factory=list)
    sparse_sources: list[str] = Field(default_factory=list)

    # Metadata
    score_version: str = '1.0'
    notes: list[str] = Field(default_factory=list)
