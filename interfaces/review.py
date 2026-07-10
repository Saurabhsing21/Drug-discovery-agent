"""Review interface and audit — combined from agents/review_interface.py and agents/review_audit.py."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from drugagent.core.schema import ReviewDecision, ReviewDecisionInput, ReviewDecisionResponse


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


# ---------------------------------------------------------------------------
# Audit (formerly agents/review_audit.py)
# ---------------------------------------------------------------------------

def persist_review_audit(run_id: str, review_decision: ReviewDecision, root: str | Path | None = None) -> str:
    audit_dir = _artifact_root(root) / "review_audit" / run_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = audit_dir / f"{timestamp}_{review_decision.reviewer_id}.review.json"
    payload = {"run_id": run_id, **review_decision.model_dump(mode="json")}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Interface (formerly agents/review_interface.py)
# ---------------------------------------------------------------------------

def persist_review_decision(run_id: str, review_decision: ReviewDecision, root: str | Path | None = None) -> str:
    decision_dir = _artifact_root(root) / "review_decisions"
    decision_dir.mkdir(parents=True, exist_ok=True)
    path = decision_dir / f"{run_id}.review_decision.json"
    payload = {"run_id": run_id, **review_decision.model_dump(mode="json")}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def load_review_decision(run_id: str, root: str | Path | None = None) -> ReviewDecision | None:
    path = _artifact_root(root) / "review_decisions" / f"{run_id}.review_decision.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("run_id", None)
    return ReviewDecision.model_validate(payload)


def apply_review_decision(payload: ReviewDecisionInput, root: str | Path | None = None) -> ReviewDecisionResponse:
    reason = (payload.reason or "").strip()
    if not reason:
        reason = f"Recorded via API (decision={payload.decision})."
    review_decision = ReviewDecision(
        decision=payload.decision,
        reviewer_id=payload.reviewer_id,
        reason=reason,
    )
    persist_review_audit(payload.run_id, review_decision, root)
    persist_review_decision(payload.run_id, review_decision, root)
    return ReviewDecisionResponse(
        run_id=payload.run_id,
        accepted=True,
        decision=payload.decision,
        status="recorded",
    )
