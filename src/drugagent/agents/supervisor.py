from __future__ import annotations

import os

from pydantic import BaseModel, Field

from drugagent.llm.policy import (
    default_reasoning_model,
    ensure_llm_available,
    llm_calls_enabled,
    llm_configured,
    require_llm_agents,
    structured_ainvoke_with_fallbacks,
)
from drugagent.memory.content import inject_content_memory
from drugagent.observability.prompt_trace import persist_prompt_trace
from drugagent.core.schema import (
    AgentReport,
    CollectorRequest,
    ConflictRecord,
    ReviewDecisionStatus,
    SourceStatus,
    StatusName,
    SupervisorAction,
    SupervisorDecision,
    VerificationReport,
)


class SupervisorDecisionResponse(BaseModel):
    action: SupervisorAction
    rationale: str
    confidence: float = 0.5
    follow_up_actions: list[str] = Field(default_factory=list)


class SupervisorAgent:
    def __init__(self, model: str | None = None, temperature: float = 0) -> None:
        self.model: str = model or os.getenv("A4T_SUPERVISOR_MODEL") or default_reasoning_model()
        self.temperature = temperature

    def _fallback(
        self,
        *,
        request: CollectorRequest,
        source_status: list[SourceStatus],
        verification_report: VerificationReport,
        conflicts: list[ConflictRecord],
        evidence_count: int,
        review_iteration_count: int,
        latest_memory_decision: str | None,
        agent_reports: list[AgentReport],
    ) -> SupervisorDecision:
        if verification_report.blocked:
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_HUMAN_REVIEW,
                rationale="Verification produced blocking issues that require human review.",
                confidence=0.92,
                follow_up_actions=["Inspect blocking verification issues before approval."],
            )
        if any(conflict.severity == "high" for conflict in conflicts):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_HUMAN_REVIEW,
                rationale="High-severity conflicts require human arbitration.",
                confidence=0.88,
                follow_up_actions=["Review cross-source contradictions."],
            )
        # If no sources are configured/requested, recollection cannot add coverage.
        # In that case, proceed to dossier/review packaging and let the review gate decide what to do.
        if not request.sources:
            return SupervisorDecision(
                action=SupervisorAction.EMIT_DOSSIER,
                rationale="No sources were requested; recollection is not applicable. Proceed to dossier emission.",
                confidence=0.74,
                follow_up_actions=["Enable sources to collect additional evidence if needed."],
            )
        successful_sources = sum(1 for status in source_status if status.status == StatusName.SUCCESS)
        if review_iteration_count < 1 and (
            evidence_count < 3
            or successful_sources < max(1, len(source_status))
            or latest_memory_decision == ReviewDecisionStatus.NEEDS_MORE_EVIDENCE.value
        ):
            return SupervisorDecision(
                action=SupervisorAction.RECOLLECT_EVIDENCE,
                rationale=(
                    f"Coverage for {request.gene_symbol} is still thin or prior memory requested more evidence."
                ),
                confidence=0.66,
                follow_up_actions=["Re-run collection to broaden evidence coverage."],
            )
        return SupervisorDecision(
            action=SupervisorAction.EMIT_DOSSIER,
            rationale="Evidence and verification state are sufficient to move to the review/dossier stage.",
            confidence=0.73,
            follow_up_actions=["Proceed to human review when required."],
        )

    async def decide(
        self,
        *,
        request: CollectorRequest,
        source_status: list[SourceStatus],
        verification_report: VerificationReport,
        conflicts: list[ConflictRecord],
        evidence_count: int,
        review_iteration_count: int,
        latest_memory_decision: str | None,
        agent_reports: list[AgentReport] | None = None,
    ) -> SupervisorDecision:
        fallback = self._fallback(
            request=request,
            source_status=source_status,
            verification_report=verification_report,
            conflicts=conflicts,
            evidence_count=evidence_count,
            review_iteration_count=review_iteration_count,
            latest_memory_decision=latest_memory_decision,
            agent_reports=list(agent_reports or []),
        )
        if not llm_calls_enabled():
            return fallback

        if not llm_configured():
            ensure_llm_available("supervisor_agent")
            return fallback
        prompt = inject_content_memory(
            (
            "You are the supervisor agent for a biomedical evidence workflow.\n"
            "Decide the next action after evidence explanation.\n"
            "Valid actions: recollect_evidence, request_human_review, emit_dossier.\n"
            "Choose recollect_evidence only if another evidence pass is justified.\n"
            "Choose request_human_review for blocked verification or serious conflicts.\n"
            "Choose emit_dossier when the workflow should proceed without recollection.\n\n"
            f"Request: gene={request.gene_symbol}, disease={request.disease_id}, objective={request.objective}\n"
            f"Evidence count: {evidence_count}\n"
            f"Review iteration count: {review_iteration_count}\n"
            f"Latest memory decision: {latest_memory_decision}\n"
            f"Verification blocked: {verification_report.blocked}\n"
            f"Blocking issues: {verification_report.blocking_issues}\n"
            f"Warning issues: {verification_report.warning_issues}\n"
            f"Conflict count: {len(conflicts)}\n"
            f"Source status: {[status.model_dump(mode='json') for status in source_status]}\n"
            f"Agent reports: {[report.model_dump(mode='json') for report in list(agent_reports or [])]}\n"
            )
        )
        persist_prompt_trace(
            run_id=request.run_id,
            agent_name="supervisor_agent",
            stage_name="supervisor_decide",
            model=self.model,
            provider=None,
            system_prompt=None,
            user_prompt=prompt,
            extra={"evidence_count": evidence_count, "conflict_count": len(conflicts)},
        )
        try:
            decision = await structured_ainvoke_with_fallbacks(
                schema=SupervisorDecisionResponse,
                prompt=prompt,
                primary_model=self.model,
                role="reasoning",
                temperature=self.temperature,
                method="function_calling",
            )
        except Exception as exc:
            if require_llm_agents():
                raise RuntimeError(f"supervisor_agent failed: {exc}") from exc
            fallback.follow_up_actions.append(f"Supervisor LLM fallback activated: {exc}")
            return fallback
        return SupervisorDecision(
            action=decision.action,
            rationale=decision.rationale,
            confidence=decision.confidence,
            follow_up_actions=decision.follow_up_actions,
            decision_mode="llm_supervisor",
            model_used=self.model,
        )
