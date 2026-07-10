from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


SCHEMA_VERSION = "0.1.0"
PLAN_VERSION = "phase1.v1"
NORMALIZATION_POLICY_VERSION = "phase1.v1"
VERIFICATION_POLICY_VERSION = "phase1.v1"
PHASE2_HANDOFF_VERSION = "phase2.v1"


class SourceName(str, Enum):
    DEPMAP = "depmap"
    PHAROS = "pharos"
    OPENTARGETS = "opentargets"
    LITERATURE = "literature"
    EXT_OPENTARGETS = "ext_opentargets"   # Official Open Targets MCP (external)
    EXT_PHAROS = "ext_pharos"             # Community Pharos MCP (external)


class StatusName(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"

from .errors import ErrorCode


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewDecisionStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"

class PlanDecisionStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


class GraphEdgeType(str, Enum):
    TARGET_DISEASE = "target_disease"
    TARGET_EVIDENCE = "target_evidence"
    EVIDENCE_PUBLICATION = "evidence_publication"
    EVIDENCE_SOURCE = "evidence_source"




class CollectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    gene_symbol: str = Field(..., min_length=1, description="Target HGNC symbol")
    disease_id: str | None = Field(default=None, description="Disease id (EFO/MONDO/etc.)")
    objective: str | None = Field(default=None, description="Optional research objective or intent.")
    species: str = Field(default="Homo sapiens")
    sources: list[SourceName] = Field(default_factory=lambda: [
        SourceName.DEPMAP,
        SourceName.PHAROS,
        SourceName.OPENTARGETS,
        SourceName.LITERATURE,
    ])
    per_source_top_k: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Target number of evidence records to keep per source when available.",
    )
    max_literature_articles: int = Field(default=5, ge=1, le=20)
    model_override: str | None = Field(default=None, description="Force the summary agent to use a specific model.")
    run_id: str = Field(default_factory=lambda: f"run-{uuid4().hex[:12]}")

    @field_validator("gene_symbol", mode="before")
    @classmethod
    def _normalize_gene_symbol(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text.upper()

    @field_validator("disease_id", mode="before")
    @classmethod
    def _normalize_disease_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        upper = text.upper()
        # Normalize common ":" form into "_" form used in the project.
        for prefix in ("EFO", "MONDO", "DOID", "OMIM", "HP"):
            if upper.startswith(prefix + ":"):
                return upper.replace(prefix + ":", prefix + "_", 1)
        return upper

    @field_validator("run_id", mode="before")
    @classmethod
    def _normalize_run_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _normalize_sources(self):
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[SourceName] = []
        for src in self.sources:
            key = src.value if hasattr(src, "value") else str(src)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(src if isinstance(src, SourceName) else SourceName(key))
        self.sources = deduped
        return self


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    endpoint: str
    query: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    evidence_id: str = Field(
        default="",
        description=(
            "Canonical, stable identifier for this evidence item (generated deterministically during normalization)."
        ),
    )
    source: SourceName
    target_id: str
    target_symbol: str
    disease_id: str | None = None
    evidence_type: str
    raw_value: float | int | str | dict[str, Any] | list[Any] | None = None
    normalization_policy_version: str | None = None
    normalized_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    support: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    provenance: Provenance
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    source: SourceName
    status: StatusName
    duration_ms: int = Field(default=0, ge=0)
    record_count: int = Field(default=0, ge=0)
    error_code: ErrorCode | None = None
    error_message: str | None = None


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    source: SourceName
    error_code: ErrorCode
    message: str
    retryable: bool = False


class CollectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str
    selected_sources: list[SourceName] = Field(default_factory=list)
    query_intent: str = Field(..., min_length=1)
    query_variants: list[str] = Field(default_factory=list)
    memory_context: dict[str, Any] = Field(default_factory=dict)
    execution_notes: list[str] = Field(default_factory=list)
    source_directives: dict[str, str] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: dict[str, list[str]] = Field(default_factory=dict)
    artifact_path: str | None = None
    plan_version: str = PLAN_VERSION
    planning_mode: str = "deterministic_fallback"
    planner_model_used: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    decision: PlanDecisionStatus
    reviewer_id: str = Field(..., min_length=1, description="Human identifier (email/username/etc.)")
    reason: str | None = Field(default=None, description="Optional rationale for the decision.")
    updated_plan: dict[str, Any] | None = Field(
        default=None,
        description="Optional full plan override as JSON (CollectionPlan-compatible). Used when decision=needs_changes.",
    )
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(..., min_length=1)
    decision: PlanDecisionStatus
    reviewer_id: str = Field(..., min_length=1)
    reason: str | None = None
    updated_plan: dict[str, Any] | None = None


class PlanDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str
    accepted: bool = True
    decision: PlanDecisionStatus
    status: str = "recorded"


class VerificationRuleOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_name: str = Field(..., min_length=1)
    passed: bool
    blocking: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    message: str | None = None


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_rules: int = Field(default=0, ge=0)
    pass_count: int = Field(default=0, ge=0)
    fail_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    blocked: bool = False
    blocking_issue_count: int = Field(default=0, ge=0)
    blocking_issues: list[str] = Field(default_factory=list)
    warning_issues: list[str] = Field(default_factory=list)
    affected_evidence_ids: list[str] = Field(default_factory=list)
    rule_outcomes: list[VerificationRuleOutcome] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verification_policy_version: str = VERIFICATION_POLICY_VERSION


class EvidenceSufficiencyReport(BaseModel):
    """Deterministic assessment of whether the collected evidence is sufficient for reporting."""

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    min_total: int = Field(default=0, ge=0)
    min_per_category: int = Field(default=0, ge=0)
    total_items: int = Field(default=0, ge=0)
    by_category: dict[str, int] = Field(default_factory=dict)
    missing_categories: list[str] = Field(default_factory=list)
    insufficient_categories: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    conflict_id: str = Field(default_factory=lambda: f"conf-{uuid4().hex[:10]}")
    severity: ConflictSeverity
    rationale: str = Field(..., min_length=1)
    sources: list[SourceName] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvidenceGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    node_type: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    edge_type: GraphEdgeType
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceGraphSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[EvidenceGraphNode] = Field(default_factory=list)
    edges: list[EvidenceGraphEdge] = Field(default_factory=list)
    graph_format: str = "json"
    artifact_path: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    decision: ReviewDecisionStatus
    reviewer_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str
    decision: ReviewDecisionStatus
    reviewer_id: str = Field(..., min_length=1)
    # UI may omit this; we will synthesize a deterministic reason on write.
    reason: str | None = None


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str
    accepted: bool
    decision: ReviewDecisionStatus
    status: str


class SupervisorAction(str, Enum):
    RECOLLECT_EVIDENCE = "recollect_evidence"
    REQUEST_HUMAN_REVIEW = "request_human_review"
    EMIT_DOSSIER = "emit_dossier"


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    action: SupervisorAction
    rationale: str = Field(..., min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    follow_up_actions: list[str] = Field(default_factory=list)
    decision_mode: str = "deterministic_fallback"
    model_used: str | None = None


class ReviewBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1)
    blocking_points: list[str] = Field(default_factory=list)
    reviewer_questions: list[str] = Field(default_factory=list)
    generation_mode: str = "deterministic_fallback"
    model_used: str | None = None


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(..., min_length=1)
    stage_name: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    decisions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    generation_mode: str = "deterministic_fallback"
    model_used: str | None = None


class Phase2HandoffPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff_version: str = PHASE2_HANDOFF_VERSION
    phase: str = "phase2"
    run_id: str
    ready: bool = False
    review_required: bool = True
    blocking_issues: list[str] = Field(default_factory=list)
    warning_issues: list[str] = Field(default_factory=list)
    conflict_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    requested_source_count: int = Field(default=0, ge=0)
    successful_source_count: int = Field(default=0, ge=0)
    dossier_artifact_path: str | None = None
    graph_artifact_path: str | None = None
    reason: str = "awaiting_human_review"


class EvidenceDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: str = SCHEMA_VERSION
    run_id: str
    query: CollectorRequest
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    source_status: list[SourceStatus] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    plan: CollectionPlan
    verified_evidence: list[EvidenceRecord] = Field(default_factory=list)
    verification_report: VerificationReport
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    graph_snapshot: EvidenceGraphSnapshot
    review_decision: ReviewDecision | None = None
    summary_markdown: str = ""
    artifact_path: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    handoff_payload: Phase2HandoffPayload
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))




class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    source: SourceName
    status: StatusName
    record_count: int = Field(default=0, ge=0)
    key_point: str
    evidence_points: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RobustnessSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_source_count: int = Field(ge=0)
    successful_source_count: int = Field(ge=0)
    used_evidence_count: int = Field(ge=0)
    source_record_depth: dict[str, int] = Field(default_factory=dict)
    minimum_coverage_met: bool = False
    failed_or_skipped_sources: list[str] = Field(default_factory=list)
    verdict: str


class LLMSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown_report: str = Field(..., description="The complete integrated therapeutic dossier.")
    robustness: RobustnessSummary | None = None
    model_used: str | None = None
    generation_mode: str = "deterministic_fallback"


class CollectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: str = SCHEMA_VERSION
    run_id: str
    query: CollectorRequest
    items: list[EvidenceRecord] = Field(default_factory=list)
    source_status: list[SourceStatus] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    llm_summary: LLMSummary | None = None
