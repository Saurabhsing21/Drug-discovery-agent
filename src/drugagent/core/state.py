from __future__ import annotations

from operator import add
from typing import Any, Annotated, TypedDict

from drugagent.core.schema import (
    AgentReport,
    CollectionPlan,
    CollectorRequest,
    CollectorResult,
    ConflictRecord,
    EvidenceSufficiencyReport,
    ErrorRecord,
    EvidenceDossier,
    EvidenceGraphSnapshot,
    EvidenceRecord,
    PlanDecision,
    ReviewBrief,
    ReviewDecision,
    SupervisorDecision,
    SourceStatus,
    VerificationReport,
)
from drugagent.scoring.schemas import ScoredTarget


class CollectorState(TypedDict, total=False):
    query: CollectorRequest
    past_runs: list[dict[str, Any]]
    input_validation_report: AgentReport
    plan: CollectionPlan
    planning_report: AgentReport
    plan_decision: PlanDecision
    evidence_sufficiency: EvidenceSufficiencyReport
    auto_recollect_count: int
    auto_recollect_pending: bool
    evidence_items: Annotated[list[EvidenceRecord], add]
    normalized_items: Annotated[list[EvidenceRecord], add]
    source_status: Annotated[list[SourceStatus], add]
    errors: Annotated[list[ErrorRecord], add]
    raw_source_payloads: Annotated[list[dict[str, Any]], add]
    source_agent_reports: Annotated[list[AgentReport], add]
    normalization_report: AgentReport
    verification_report: VerificationReport
    verification_agent_report: AgentReport
    conflicts: Annotated[list[ConflictRecord], add]
    conflict_agent_report: AgentReport
    evidence_graph: EvidenceGraphSnapshot
    graph_agent_report: AgentReport
    explanation: str
    summary_agent_report: AgentReport
    supervisor_decision: SupervisorDecision
    review_brief: ReviewBrief
    review_iteration_count: int
    review_recollection_pending: bool
    review_decision: ReviewDecision
    final_result: CollectorResult
    final_dossier: EvidenceDossier
    dossier_agent_report: AgentReport
    scored_target: ScoredTarget
    evidence_dashboard_path: str
