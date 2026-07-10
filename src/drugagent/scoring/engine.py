from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from drugagent.core.schema import CollectorRequest, ConflictRecord, EvidenceRecord, VerificationReport


def category_for_evidence(item: EvidenceRecord) -> str:
    source = item.source.value if hasattr(item.source, "value") else str(item.source)
    t = (item.evidence_type or "").strip().lower()
    if source == "literature" or "literature" in t or "pubmed" in t or "pmid" in t:
        return "literature"
    if "annot" in t or "uniprot" in t or "protein" in t or "tdl" in t:
        return "annotation"
    if "depend" in t or "depmap" in t or "crispr" in t:
        return "dependency"
    if "disease" in t or "association" in t or "opentarget" in t or "ot_" in t:
        return "disease_association"
    return "other"


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def record_quality(item: EvidenceRecord) -> float:
    conf = float(item.confidence or 0.0)
    score = item.normalized_score
    if score is None:
        return _clamp01(0.6 * conf)
    return _clamp01((0.6 * float(score)) + (0.4 * conf))


def _severity_penalty(sev: Any) -> float:
    value = sev.value if hasattr(sev, "value") else str(sev)
    value = (value or "").strip().lower()
    if value == "high":
        return 0.10
    if value == "medium":
        return 0.05
    if value == "low":
        return 0.02
    return 0.0


class DecisionSummaryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    strength: str
    category_score: float = Field(ge=0.0, le=1.0)
    top_evidence_id: str | None = None
    top_finding: str | None = None
    main_limitation: str | None = None


class ScoredDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_support_score: float = Field(ge=0.0, le=1.0)
    evidence_completeness_score: float = Field(ge=0.0, le=1.0)
    contradiction_penalty: float = Field(ge=0.0, le=1.0)
    decision_status: str
    category_scores: dict[str, float] = Field(default_factory=dict)
    summary_rows: list[DecisionSummaryRow] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _CategoryAggregate:
    items: list[EvidenceRecord]
    score: float


def _strength_bucket(score: float) -> str:
    if score >= 0.75:
        return "strong"
    if score >= 0.5:
        return "medium"
    return "weak"


def _truncate(text: str, max_len: int = 140) -> str:
    t = (text or "").replace("\n", " ").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def score_evidence(
    *,
    request: CollectorRequest,
    items: list[EvidenceRecord],
    conflicts: list[ConflictRecord],
    verification_report: VerificationReport | None,
    required_categories: tuple[str, ...] = ("annotation", "dependency", "disease_association", "literature"),
) -> ScoredDecision:
    by_cat: dict[str, list[EvidenceRecord]] = {c: [] for c in required_categories}
    other: list[EvidenceRecord] = []
    for it in items:
        cat = category_for_evidence(it)
        if cat in by_cat:
            by_cat[cat].append(it)
        else:
            other.append(it)

    aggregates: dict[str, _CategoryAggregate] = {}
    for cat in required_categories:
        cat_items = sorted(by_cat.get(cat, []), key=record_quality, reverse=True)
        top = cat_items[:3]
        score = float(sum(record_quality(x) for x in top) / len(top)) if top else 0.0
        aggregates[cat] = _CategoryAggregate(items=cat_items, score=_clamp01(score))

    present = sum(1 for cat in required_categories if aggregates[cat].items)
    completeness = present / max(1, len(required_categories))

    contradiction_penalty = 0.0
    worst = 0.0
    for c in conflicts or []:
        p = _severity_penalty(getattr(c, "severity", None))
        worst = max(worst, p)
    contradiction_penalty = _clamp01(worst)

    # Category weights (tunable later); keep literature lowest by default.
    weights = {
        "annotation": 0.25,
        "dependency": 0.30,
        "disease_association": 0.30,
        "literature": 0.15,
    }
    weighted_sum = sum(weights[c] * aggregates[c].score for c in required_categories)
    overall = _clamp01(weighted_sum - contradiction_penalty)

    has_high_conflict = any((_severity_penalty(getattr(c, "severity", None)) >= 0.10) for c in (conflicts or []))

    if overall >= 0.75 and completeness >= 0.75:
        status = "Strongly Supported"
    elif overall >= 0.60 and completeness >= 0.50:
        status = "Supported with Context Limits"
    elif overall >= 0.45:
        status = "Mixed / Context-Dependent"
    elif overall >= 0.25:
        status = "Weakly Supported"
    else:
        status = "Not Supported"

    if has_high_conflict and status == "Strongly Supported":
        status = "Supported with Context Limits"
    elif has_high_conflict and status == "Supported with Context Limits":
        status = "Mixed / Context-Dependent"

    summary_rows: list[DecisionSummaryRow] = []
    for cat in required_categories:
        agg = aggregates[cat]
        top_item = agg.items[0] if agg.items else None
        limitation: str | None = None
        if not agg.items:
            limitation = "missing coverage in this category"
        elif cat == "literature":
            limitation = "literature is supportive and can be noisy"
        elif len(agg.items) == 1:
            limitation = "single-item evidence; may not generalize"
        elif sum(1 for it in agg.items if float(it.confidence or 0.0) >= 0.75) == 0:
            limitation = "no high-confidence items detected"
        elif cat == "disease_association" and not request.disease_id:
            limitation = "disease context unspecified; association may be non-specific"

        summary_rows.append(
            DecisionSummaryRow(
                category=cat,
                strength=_strength_bucket(agg.score),
                category_score=agg.score,
                top_evidence_id=(top_item.evidence_id if top_item else None),
                top_finding=_truncate(top_item.summary or top_item.evidence_type) if top_item else None,
                main_limitation=limitation,
            )
        )

    next_steps: list[str] = []
    missing = [cat for cat in required_categories if not aggregates[cat].items]
    if "annotation" in missing:
        next_steps.append("Add target tractability/annotation coverage (PHAROS).")
    if "dependency" in missing:
        next_steps.append("Collect functional dependency evidence (DepMap).")
    if "disease_association" in missing:
        next_steps.append("Collect disease association evidence (Open Targets).")
    if "literature" in missing:
        next_steps.append("Collect focused literature evidence (target + indication keywords).")
    if has_high_conflict:
        next_steps.append("Resolve context-specific contradictions; stratify by disease subtype/mutation context.")
    if verification_report is not None and verification_report.blocked:
        next_steps.append("Resolve blocking verification issues before drawing conclusions.")

    return ScoredDecision(
        overall_support_score=overall,
        evidence_completeness_score=_clamp01(completeness),
        contradiction_penalty=contradiction_penalty,
        decision_status=status,
        category_scores={cat: aggregates[cat].score for cat in required_categories},
        summary_rows=summary_rows,
        recommended_next_steps=next_steps,
    )

