from __future__ import annotations

import os

from pydantic import BaseModel, Field

from drugagent.core.schema import AgentReport, EvidenceRecord


class NormalizationResponse(BaseModel):
    summary: str = Field(..., min_length=1)
    decisions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class NormalizationAgent:
    def __init__(self, model: str | None = None, temperature: float = 0) -> None:
        self.model = model or os.getenv("A4T_NORMALIZATION_AGENT_MODEL", "gpt-5-mini")
        self.temperature = temperature

    def _fallback(self, raw_count: int, normalized_items: list[EvidenceRecord]) -> AgentReport:
        return AgentReport(
            agent_name="normalization_agent",
            stage_name="normalize_evidence",
            summary=f"Normalized {len(normalized_items)} of {raw_count} raw evidence record(s) into canonical schema.",
            decisions=[
                "Standardized target symbols to uppercase canonical form.",
                "Clamped normalized scores and confidence into [0,1].",
                "Attached normalization policy version to each normalized record.",
            ],
            risks=[],
            next_actions=["Pass normalized evidence into verification agent."],
            structured_payload={
                "raw_count": raw_count,
                "normalized_count": len(normalized_items),
            },
        )

    async def review(self, *, raw_count: int, normalized_items: list[EvidenceRecord]) -> AgentReport:
        """
        Report on the normalization pass.
        LLM call removed: the normalization transform is 100% deterministic.
        The LLM output was identical to the fallback (just counting items).
        """
        return self._fallback(raw_count, normalized_items)
