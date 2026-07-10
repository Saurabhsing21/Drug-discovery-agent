from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from drugagent.llm.policy import (
    default_reasoning_model,
    ensure_llm_available,
    google_api_key,
    llm_calls_enabled,
    openai_api_key,
    require_llm_planner,
    structured_ainvoke_with_fallbacks,
)
from drugagent.memory.content import inject_content_memory
from drugagent.observability.prompt_trace import persist_prompt_trace
from drugagent.core.schema import CollectorRequest, SourceName


class SourceDirective(BaseModel):
    source: str
    directive: str


class PlanningResponse(BaseModel):
    query_intent: str
    query_variants: list[str]
    source_order: list[str]
    source_directives: list[SourceDirective]
    execution_notes: list[str]

    def directives_dict(self) -> dict[str, str]:
        return {entry.source: entry.directive for entry in self.source_directives if entry.source and entry.directive}


class PlanningAgent:
    """LLM planner used for strategy, ordering, and per-source directives."""

    def __init__(self, model: str | None = None, temperature: float = 0) -> None:
        self.model: str = model or os.getenv("A4T_PLANNER_MODEL") or default_reasoning_model()
        self.temperature = temperature

    def _default_response(
        self,
        request: CollectorRequest,
        selected_sources: list[SourceName],
        memory_context: dict[str, Any],
        execution_notes: list[str],
        query_variants: list[str],
        fallback_reason: str | None = None,
    ) -> PlanningResponse:
        fallback_notes = list(execution_notes)
        if fallback_reason:
            fallback_notes.append(f"LLM planner fallback activated: {fallback_reason}")
        latest_decision = str(memory_context.get("latest_review_decision") or "")
        memory_hint = ""
        if latest_decision == "needs_more_evidence":
            memory_hint = " Prior run requested more evidence; prioritize broad coverage and non-literature sources first."

        directives = []
        for source in selected_sources:
            directives.append(
                SourceDirective(
                    source=source.value,
                    directive=(
                        f"Collect {request.per_source_top_k} evidence records for {request.gene_symbol}"
                        + (f" in context {request.disease_id}" if request.disease_id else "")
                        + "."
                        + memory_hint
                    ),
                )
            )
        query_intent = (
            f"Collect Phase-1 evidence for target {request.gene_symbol}"
            + (f" in disease context {request.disease_id}" if request.disease_id else "")
            + (
                f". Memory-informed planning enabled from {memory_context['match_count']} prior run(s)"
                if memory_context.get("match_count")
                else ""
            )
            + "."
        )
        return PlanningResponse(
            query_intent=query_intent,
            query_variants=query_variants,
            source_order=[source.value for source in selected_sources],
            source_directives=directives,
            execution_notes=fallback_notes,
        )

    async def plan(
        self,
        *,
        request: CollectorRequest,
        selected_sources: list[SourceName],
        memory_context: dict[str, Any],
        execution_notes: list[str],
        query_variants: list[str],
    ) -> tuple[PlanningResponse, str, str | None]:
        fallback = self._default_response(
            request=request,
            selected_sources=selected_sources,
            memory_context=memory_context,
            execution_notes=execution_notes,
            query_variants=query_variants,
        )

        if not llm_calls_enabled():
            return fallback, "deterministic_fallback", None

        if not openai_api_key() and not google_api_key():
            ensure_llm_available("planning_agent")
            return fallback, "deterministic_fallback", None

        prompt = inject_content_memory(
            (
            "You are the Phase-1 planning agent for a biomedical evidence workflow.\n"
            "Return a plan for evidence collection.\n"
            "Rules:\n"
            "- Only use requested sources.\n"
            "- Keep query_intent concise and operational.\n"
            "- Keep query_variants grounded in the supplied aliases/objective.\n"
            "- source_order must contain only requested sources.\n"
            "- source_directives must explain what each source should focus on.\n"
            "- execution_notes should mention memory context when relevant.\n\n"
            f"Request: gene={request.gene_symbol}, disease={request.disease_id}, objective={request.objective}\n"
            f"Requested sources: {[source.value for source in selected_sources]}\n"
            f"Candidate query variants: {query_variants}\n"
            f"Memory context: {memory_context}\n"
            f"Existing execution notes: {execution_notes}\n"
            )
        )
        persist_prompt_trace(
            run_id=request.run_id,
            agent_name="planning_agent",
            stage_name="plan_collection",
            model=self.model,
            provider=None,
            system_prompt=None,
            user_prompt=prompt,
            extra={"selected_sources": [s.value for s in selected_sources]},
        )

        try:
            response = await structured_ainvoke_with_fallbacks(
                schema=PlanningResponse,
                prompt=prompt,
                primary_model=self.model,
                role="reasoning",
                temperature=self.temperature,
                method="function_calling",
            )
        except Exception as exc:
            if require_llm_planner():
                raise RuntimeError(f"planning_agent failed: {exc}") from exc
            fallback = self._default_response(
                request=request,
                selected_sources=selected_sources,
                memory_context=memory_context,
                execution_notes=execution_notes,
                query_variants=query_variants,
                fallback_reason=str(exc),
            )
            return fallback, "deterministic_fallback", None

        allowed = {source.value for source in selected_sources}
        ordered = [value for value in response.source_order if value in allowed]
        for source in [source.value for source in selected_sources]:
            if source not in ordered:
                ordered.append(source)

        response.query_variants = list(dict.fromkeys([variant for variant in response.query_variants if variant])) or query_variants
        response.source_order = ordered
        directive_map: dict[str, str] = {}
        for entry in response.source_directives:
            source = (entry.source or "").strip()
            directive = (entry.directive or "").strip()
            if source in allowed and directive:
                directive_map[source] = directive
        fallback_map = fallback.directives_dict()
        for source in ordered:
            directive_map.setdefault(source, fallback_map.get(source, f"Collect evidence for {request.gene_symbol}."))
        response.source_directives = [SourceDirective(source=source, directive=directive_map[source]) for source in ordered]
        if not response.execution_notes:
            response.execution_notes = execution_notes
        if not response.query_intent:
            response.query_intent = fallback.query_intent
        return response, "llm_planner", self.model
