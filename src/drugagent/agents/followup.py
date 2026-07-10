from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field

from drugagent.memory.content import inject_content_memory
from drugagent.llm.policy import ensure_llm_available, get_llm, llm_calls_enabled, llm_configured, require_llm_agents


class FollowupContext(BaseModel):
    run_id: str = Field(..., min_length=1)
    gene_symbol: str = Field(..., min_length=1)
    disease_id: str | None = None
    original_objective: str | None = None
    dossier_summary_markdown: str = ""
    evidence_index: list[dict[str, Any]] = Field(default_factory=list)
    url_resources: list[dict[str, Any]] = Field(default_factory=list)


class FollowupResponse(BaseModel):
    answer_markdown: str = Field(..., min_length=1)


class FollowupAgent:
    def __init__(self, model: str | None = None, temperature: float = 0.2) -> None:
        default_fast = os.getenv("A4T_OPENAI_FAST_MODEL") or "gpt-5-mini"
        self.model: str = model or os.getenv("A4T_FOLLOWUP_MODEL") or default_fast
        self.temperature = temperature

    async def answer(self, *, question: str, context: FollowupContext) -> FollowupResponse:
        question = (question or "").strip()
        if not question:
            return FollowupResponse(answer_markdown="(No follow-up question provided.)")

        if not llm_calls_enabled():
            if require_llm_agents():
                raise RuntimeError("Follow-up requires LLM calls but A4T_LLM_CALLS_ENABLED=0.")
            raise RuntimeError("Follow-up requires LLM calls (no deterministic fallback).")

        if not llm_configured():
            ensure_llm_available("followup_agent")
            raise RuntimeError("Follow-up requires a configured LLM (no deterministic fallback).")

        system = inject_content_memory(
            (
                "You are a drug discovery follow-up assistant.\n"
                "Answer the user's follow-up question using ONLY the provided context:\n"
                "- the original run's dossier summary\n"
                "- a compact evidence index (canonical evidence IDs + summaries + key numbers)\n"
                "- optional user-provided URL snippets\n\n"
                "Rules:\n"
                "- If you cite or rely on an evidence item, include its canonical evidence id in brackets.\n"
                "- If you rely on a URL snippet, cite it in a 'References' section under 'User URLs'.\n"
                "- If the context is insufficient, say what is missing and suggest what to collect next.\n"
                "- Return Markdown only.\n"
                "- End with a '## References' section that includes:\n"
                "  - '### Evidence IDs' (bullets with bracketed evidence ids)\n"
                "  - '### User URLs' (bullets with URLs actually used)\n"
            )
        )

        payload = {
            "run_id": context.run_id,
            "target": context.gene_symbol,
            "disease_id": context.disease_id,
            "original_objective": context.original_objective,
            "dossier_summary_markdown": context.dossier_summary_markdown,
            "evidence_index": context.evidence_index,
            "url_resources": context.url_resources,
            "question": question,
        }

        user = "Follow-up context payload:\n\n" + json.dumps(payload, indent=2)

        llm = get_llm(model=self.model, temperature=self.temperature)
        resp = await llm.ainvoke([("system", system), ("user", user)])
        text = str(resp.content or "").strip()
        if not text:
            raise RuntimeError("FollowupAgent returned empty response.")
        if "## References" not in text:
            raise RuntimeError("FollowupAgent response missing required '## References' section.")
        return FollowupResponse(answer_markdown=text)
