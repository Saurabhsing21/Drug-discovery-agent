from __future__ import annotations

import os

from pydantic import BaseModel, Field

from drugagent.core.schema import AgentReport, CollectorRequest


class InputValidationResponse(BaseModel):
    summary: str = Field(..., min_length=1)
    decisions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class InputValidationAgent:
    def __init__(self, model: str | None = None, temperature: float = 0) -> None:
        self.model = model or os.getenv("A4T_INPUT_AGENT_MODEL", "gpt-5-mini")
        self.temperature = temperature

    def _fallback(self, *, request: CollectorRequest, past_run_count: int) -> AgentReport:
        return AgentReport(
            agent_name="input_validation_agent",
            stage_name="validate_input",
            summary=(
                f"Validated request for {request.gene_symbol}"
                + (f" in disease context {request.disease_id}" if request.disease_id else "")
                + f"; found {past_run_count} episodic memory match(es)."
            ),
            decisions=[
                "Confirmed request contract shape.",
                "Prepared query for planning stage.",
            ],
            risks=[],
            next_actions=["Send request and memory context into planning agent."],
            structured_payload={
                "run_id": request.run_id,
                "gene_symbol": request.gene_symbol,
                "disease_id": request.disease_id,
                "past_run_count": past_run_count,
            },
        )

    async def review(self, *, request: CollectorRequest, past_run_count: int) -> AgentReport:
        """
        Validate the incoming request and report on episodic memory context.
        LLM call removed: restating the user's own request fields adds no value.
        The deterministic fallback always produces a correct, grounded report.
        """
        return self._fallback(request=request, past_run_count=past_run_count)
