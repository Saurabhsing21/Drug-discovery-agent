"""Review and plan decision router."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException
from agents.plan_interface import apply_plan_decision
from agents.review_interface import apply_review_decision
from api.event_bus import BUS
from api.models import PlanDecisionBody, ReviewDecisionBody

router = APIRouter()


@router.post("/api/runs/{run_id}/plan-decision")
async def post_plan_decision(run_id: str, body: PlanDecisionBody) -> dict[str, Any]:
    if body.run_id != run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")
    resp = apply_plan_decision(body)
    BUS.publish(run_id, "plan_decision_recorded", resp.model_dump(mode="json"))
    return resp.model_dump(mode="json")


@router.post("/api/runs/{run_id}/review-decision")
async def post_review_decision(run_id: str, body: ReviewDecisionBody) -> dict[str, Any]:
    if body.run_id != run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")
    resp = apply_review_decision(body)
    BUS.publish(run_id, "review_decision_recorded", resp.model_dump(mode="json"))
    return resp.model_dump(mode="json")
