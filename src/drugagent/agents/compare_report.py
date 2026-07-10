from __future__ import annotations

from dataclasses import dataclass

import drugagent.llm.prompts as prompts
from drugagent.llm.policy import (
    ainvoke_with_fallbacks,
    default_fast_model,
    ensure_llm_available,
    llm_calls_enabled,
    llm_configured,
    require_llm_agents,
)


@dataclass
class CompareReportAgent:
    model: str | None = None
    temperature: float = 0.2

    async def run(self, *, report_a: str, report_b: str, title_a: str, title_b: str) -> str:
        if not llm_calls_enabled():
            if require_llm_agents():
                raise RuntimeError("LLM calls are disabled (A4T_LLM_CALLS_ENABLED=0) but A4T_REQUIRE_LLM_AGENTS=1.")
            return self._deterministic_fallback(report_a, report_b, title_a, title_b)

        if not llm_configured():
            ensure_llm_available("compare_report_agent")
            return self._deterministic_fallback(report_a, report_b, title_a, title_b)

        system_prompt = prompts.get_system_prompt_compare()
        user_prompt = prompts.get_user_prompt_compare(title_a, title_b, report_a, report_b)

        response = await ainvoke_with_fallbacks(
            prompt=[("system", system_prompt), ("user", user_prompt)],
            primary_model=self.model or default_fast_model(),
            role="fast",
            temperature=self.temperature,
        )
        content = getattr(response, "content", str(response))
        if isinstance(content, list):
            content = "".join([m.get("text", "") for m in content if isinstance(m, dict)])
        return content if isinstance(content, str) and content.strip() else str(response)

    def _deterministic_fallback(self, report_a: str, report_b: str, title_a: str, title_b: str) -> str:
        return (
            "# COMPARATIVE THERAPEUTIC TARGET REPORT\n\n"
            f"Genes Compared: {title_a} vs {title_b}\n\n"
            "---\n\n"
            "## 1. Executive Comparison Summary\n\n"
            "- LLM unavailable; unable to generate structured comparison.\n\n"
            "---\n\n"
            "## 2. Side-by-Side Evidence Comparison\n\n"
            "### 2.1 Target Annotation (PHAROS)\n"
            "| Metric | Gene A | Gene B | Winner | Insight |\n"
            "|--------|--------|--------|--------|---------|\\n"
            "| TDL | | | | |\n"
            "| Target Family | | | | |\n"
            "| Ligand Count | | | | |\n"
            "| Novelty | | | | |\n\n"
            "### 2.2 Genetic Dependency (DepMap)\n"
            "| Metric | Gene A | Gene B | Winner | Insight |\n"
            "|--------|--------|--------|--------|---------|\\n"
            "| Avg Gene Effect | | | | |\n"
            "| Strong Dependency Fraction | | | | |\n"
            "| Max Dependency Strength | | | | |\n\n"
            "### 2.3 Disease Associations (Open Targets)\n"
            "| Metric | Gene A | Gene B | Winner | Insight |\n"
            "|--------|--------|--------|--------|---------|\\n"
            "| Top Score | | | | |\n"
            "| Disease Breadth | | | | |\n"
            "| Oncology Relevance | | | | |\n\n"
            "### 2.4 Literature Strength\n"
            "| Metric | Gene A | Gene B | Winner | Insight |\n"
            "|--------|--------|--------|--------|---------|\\n"
            "| # High-impact Papers | | | | |\n"
            "| Citation Strength | | | | |\n"
            "| Research Maturity | | | | |\n\n"
            "---\n\n"
            "## 3. Cross-Domain Interpretation\n\n"
            "- LLM unavailable; analysis not generated.\n\n"
            "## 4. Therapeutic Positioning Insight\n\n"
            "- LLM unavailable; analysis not generated.\n\n"
            "## 5. Risk Assessment\n\n"
            "| Risk Type | Gene A | Gene B | Insight |\n"
            "|----------|--------|--------|--------|\n"
            "| Biological Risk | | | |\n"
            "| Clinical Translation Risk | | | |\n"
            "| Competition / Saturation | | | |\n\n"
            "## 6. Final Verdict (CRITICAL)\n\n"
            "- LLM unavailable; verdict not generated.\n\n"
            "## 7. Confidence Score\n\n"
            "- Confidence: 0.0\n"
            "- Reason: LLM unavailable.\n"
        )
