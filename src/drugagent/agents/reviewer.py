from __future__ import annotations

import os

from pydantic import BaseModel, Field

from drugagent.llm.policy import (
    default_fast_model,
    ensure_llm_available,
    llm_calls_enabled,
    llm_configured,
    require_llm_agents,
    structured_ainvoke_with_fallbacks,
)
from drugagent.memory.content import inject_content_memory
from drugagent.observability.prompt_trace import persist_prompt_trace
from drugagent.core.schema import CollectorRequest, ConflictRecord, ReviewBrief, SourceStatus, VerificationReport


class ReviewBriefResponse(BaseModel):
    summary: str = Field(..., min_length=1)
    blocking_points: list[str] = Field(default_factory=list)
    reviewer_questions: list[str] = Field(default_factory=list)


class ReviewSupportAgent:
    def __init__(self, model: str | None = None, temperature: float = 0) -> None:
        self.model: str = model or os.getenv("A4T_REVIEW_SUPPORT_MODEL") or default_fast_model()
        self.temperature = temperature

    def _fallback(
        self,
        *,
        request: CollectorRequest,
        source_status: list[SourceStatus],
        verification_report: VerificationReport,
        conflicts: list[ConflictRecord],
        explanation: str,
    ) -> ReviewBrief:
        blocking_points = list(verification_report.blocking_issues)
        if conflicts:
            blocking_points.append(f"{len(conflicts)} conflict(s) detected across sources.")
        return ReviewBrief(
            summary=(
                f"Review {request.gene_symbol}"
                + (f" in disease context {request.disease_id}" if request.disease_id else "")
                + f". Sources executed={len(source_status)}, blocked={verification_report.blocked}, conflicts={len(conflicts)}."
            ),
            blocking_points=blocking_points,
            reviewer_questions=[
                "Is the evidence package sufficient to approve downstream handoff?",
                "Do any conflicts or missing coverage require additional evidence collection?",
            ],
            generation_mode="deterministic_fallback",
        )

    async def build(
        self,
        *,
        request: CollectorRequest,
        source_status: list[SourceStatus],
        verification_report: VerificationReport,
        conflicts: list[ConflictRecord],
        explanation: str,
    ) -> ReviewBrief:
        fallback = self._fallback(
            request=request,
            source_status=source_status,
            verification_report=verification_report,
            conflicts=conflicts,
            explanation=explanation,
        )
        if not llm_calls_enabled():
            return fallback

        if not llm_configured():
            ensure_llm_available("review_support_agent")
            return fallback
        prompt = inject_content_memory(
            (
            "You are the review-support agent for a biomedical evidence workflow.\n"
            "Prepare a concise review packet for a human scientist.\n"
            "Return a summary, blocking points, and reviewer questions.\n\n"
            f"Request: gene={request.gene_symbol}, disease={request.disease_id}, objective={request.objective}\n"
            f"Verification blocked: {verification_report.blocked}\n"
            f"Blocking issues: {verification_report.blocking_issues}\n"
            f"Warning issues: {verification_report.warning_issues}\n"
            f"Conflict count: {len(conflicts)}\n"
            f"Summary excerpt: {explanation[:1200]}\n"
            )
        )
        persist_prompt_trace(
            run_id=request.run_id,
            agent_name="review_support_agent",
            stage_name="prepare_review_brief",
            model=self.model,
            provider=None,
            system_prompt=None,
            user_prompt=prompt,
            extra={"conflict_count": len(conflicts), "blocked": verification_report.blocked},
        )
        try:
            response = await structured_ainvoke_with_fallbacks(
                schema=ReviewBriefResponse,
                prompt=prompt,
                primary_model=self.model,
                role="fast",
                temperature=self.temperature,
                method="function_calling",
            )
        except Exception as exc:
            if require_llm_agents():
                raise RuntimeError(f"review_support_agent failed: {exc}") from exc
            fallback.blocking_points.append(f"Review-support LLM fallback activated: {exc}")
            return fallback
        return ReviewBrief(
            summary=response.summary,
            blocking_points=response.blocking_points,
            reviewer_questions=response.reviewer_questions,
            generation_mode="llm_review_support",
            model_used=self.model,
        )
