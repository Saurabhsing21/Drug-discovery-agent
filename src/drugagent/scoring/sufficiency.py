from __future__ import annotations

import os
from collections import Counter
from typing import Iterable

from drugagent.core.schema import EvidenceRecord, EvidenceSufficiencyReport
from drugagent.scoring.engine import category_for_evidence


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
        return max(0, value)
    except Exception:
        return default


def assess_evidence_sufficiency(
    items: Iterable[EvidenceRecord],
    *,
    min_total: int | None = None,
    min_per_category: int | None = None,
    required_categories: Iterable[str] = ("annotation", "dependency", "disease_association", "literature"),
) -> EvidenceSufficiencyReport:
    """Return a deterministic sufficiency report for the current evidence set.

    Defaults are env-configurable:
      - A4T_EVIDENCE_MIN_TOTAL (default len(required_categories) * min_per_category)
      - A4T_EVIDENCE_MIN_PER_CATEGORY (default 1)
    """
    resolved_min_per_category = (
        min_per_category if min_per_category is not None else _int_env("A4T_EVIDENCE_MIN_PER_CATEGORY", 1)
    )
    normalized_required = [str(c).strip().lower() for c in required_categories if str(c).strip()]
    default_min_total = max(0, len(normalized_required) * max(0, int(resolved_min_per_category)))
    resolved_min_total = min_total if min_total is not None else _int_env("A4T_EVIDENCE_MIN_TOTAL", default_min_total)

    item_list = list(items)
    total = len(item_list)

    categories = [category_for_evidence(item) for item in item_list]
    counts = Counter(categories)
    by_category = {k: int(v) for k, v in sorted(counts.items(), key=lambda kv: kv[0])}

    missing = [c for c in normalized_required if counts.get(c, 0) <= 0]
    insufficient = [c for c in normalized_required if 0 < counts.get(c, 0) < resolved_min_per_category]

    reasons: list[str] = []
    if total < resolved_min_total:
        reasons.append(f"Total evidence items {total} is below the minimum {resolved_min_total}.")
    if missing:
        reasons.append(f"Missing required evidence categories: {', '.join(missing)}.")
    if insufficient:
        reasons.append(
            "Insufficient evidence items for categories: "
            + ", ".join(f"{c}({counts.get(c, 0)}/{resolved_min_per_category})" for c in insufficient)
            + "."
        )

    sufficient = (total >= resolved_min_total) and (not missing) and (not insufficient)

    return EvidenceSufficiencyReport(
        sufficient=sufficient,
        min_total=resolved_min_total,
        min_per_category=resolved_min_per_category,
        total_items=total,
        by_category=by_category,
        missing_categories=missing,
        insufficient_categories=insufficient,
        reasons=reasons,
    )


def resolve_auto_recollect_policy() -> dict[str, int]:
    return {
        "max_passes": _int_env("A4T_AUTO_RECOLLECT_MAX_PASSES", 1),
        "top_k_step": _int_env("A4T_AUTO_RECOLLECT_TOP_K_STEP", 5),
        "lit_step": _int_env("A4T_AUTO_RECOLLECT_LIT_STEP", 5),
    }


def maybe_apply_auto_recollect(
    *,
    per_source_top_k: int,
    max_literature_articles: int,
    sufficiency: EvidenceSufficiencyReport,
    blocked: bool,
    high_conflict: bool,
    auto_recollect_count: int,
    max_passes: int,
    top_k_step: int,
    lit_step: int,
) -> dict[str, int | bool]:
    should_recollect = (
        (not blocked)
        and (not high_conflict)
        and (not sufficiency.sufficient)
        and (auto_recollect_count < max(0, max_passes))
    )

    missing_non_lit = [
        c
        for c in (sufficiency.missing_categories + sufficiency.insufficient_categories)
        if c and c != "literature"
    ]
    missing_lit = "literature" in (sufficiency.missing_categories + sufficiency.insufficient_categories)

    bump_top_k = bool(should_recollect and missing_non_lit)
    bump_lit = bool(should_recollect and missing_lit)
    should_recollect = bool(should_recollect and (bump_top_k or bump_lit))

    # Never bump both unless both are missing.
    next_top_k = min(20, int(per_source_top_k) + max(0, top_k_step)) if bump_top_k else int(per_source_top_k)
    next_lit = min(20, int(max_literature_articles) + max(0, lit_step)) if bump_lit else int(max_literature_articles)
    next_count = int(auto_recollect_count) + 1 if should_recollect else int(auto_recollect_count)
    return {
        "should_recollect": bool(should_recollect),
        "next_per_source_top_k": int(next_top_k),
        "next_max_literature_articles": int(next_lit),
        "next_auto_recollect_count": int(next_count),
    }
