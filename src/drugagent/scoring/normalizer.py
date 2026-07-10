from __future__ import annotations

from typing import Iterable

from drugagent.core.schema import EvidenceRecord, NORMALIZATION_POLICY_VERSION
from drugagent.evidence.id import compute_evidence_id


def _clamp_score(value: float | int | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    parsed = float(value)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def normalize_evidence_item(item: EvidenceRecord) -> EvidenceRecord:
    normalized_score = _clamp_score(item.normalized_score)
    if normalized_score is None and isinstance(item.raw_value, (int, float)):
        normalized_score = _clamp_score(item.raw_value)
    confidence = _clamp_score(item.confidence, default=0.5)
    if confidence is None:
        confidence = 0.5

    source_name = item.source.value if hasattr(item.source, "value") else str(item.source)
    evidence_id = compute_evidence_id(
        source=source_name,
        target_id=item.target_id,
        disease_id=item.disease_id,
        evidence_type=item.evidence_type,
        raw_value=item.raw_value,
        support=item.support,
    )

    return item.model_copy(
        update={
            "evidence_id": evidence_id,
            "target_symbol": item.target_symbol.strip().upper(),
            "disease_id": item.disease_id or None,
            "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
            "normalized_score": normalized_score,
            "confidence": confidence,
            "summary": item.summary.strip() if item.summary else item.summary,
        }
    )


def normalize_evidence_items(items: Iterable[EvidenceRecord]) -> list[EvidenceRecord]:
    return [normalize_evidence_item(item) for item in items]
