from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from drugagent.llm.policy import ensure_llm_available, llm_calls_enabled, llm_configured, require_llm_agents, structured_runnable


ModeName = Literal["new_run", "followup"]


class QueryInterpretationContext(BaseModel):
    active_gene: str | None = None
    active_disease: str | None = None
    mode: ModeName = "new_run"


class QueryInterpretationResult(BaseModel):
    in_scope: bool
    gene_symbol: str | None = None
    disease_id: str | None = None
    objective: str = Field(..., min_length=1)
    detected_urls: list[str] = Field(default_factory=list)
    target_switch_detected: bool = False
    user_message_to_show_if_out_of_scope: str = Field(..., min_length=1)


_OUT_OF_SCOPE_MESSAGE = (
    "I'm a drug discovery / therapeutic target evidence agent. "
    "Please ask about a gene/target (e.g., KRAS, EGFR), a disease/indication, "
    "mutations (e.g., KRAS G12C), dependency, tractability, or literature evidence."
)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def extract_urls(text: str) -> list[str]:
    # Simple URL extractor; final validation happens in UrlResourceFetcher.
    if not text:
        return []
    urls = re.findall(r"https?://[^\s)\\}}>]+", text, flags=re.IGNORECASE)
    return _dedupe_keep_order([u.strip().rstrip(".,;") for u in urls if u.strip()])


def _strip_urls(text: str) -> str:
    for url in extract_urls(text):
        text = text.replace(url, " ")
    return re.sub(r"\s+", " ", text).strip()


_DISEASE_ID_RE = re.compile(r"\b([A-Za-z]{2,15})[:_](\d{2,10})\b")


def extract_disease_id(text: str) -> str | None:
    if not text:
        return None
    for m in _DISEASE_ID_RE.finditer(text):
        prefix = m.group(1)
        num = m.group(2)
        # Only accept common ontology/id prefixes to avoid random tokens.
        if prefix.lower() in {
            "efo",
            "mondo",
            "orphanet",
            "doid",
            "hp",
            "omim",
        }:
            return f"{prefix.upper()}_{num}" if ":" not in m.group(0) and "_" in m.group(0) else m.group(0)
    return None


_GENE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]{1,11}\b")
_MUTATION_TOKEN_RE = re.compile(r"^[A-Z]\d{1,4}[A-Z]$", flags=re.IGNORECASE)

# Very small stoplist to avoid common false-positives when we do not have a gene dictionary.
_STOP_TOKENS = {
    "DNA",
    "RNA",
    "CELL",
    "CELLS",
    "CANCER",
    "TUMOR",
    "TUMOUR",
    "DRUG",
    "DRUGS",
    "TARGET",
    "THERAPY",
    "THERAPEUTIC",
    "MUTATION",
    "MUTATIONS",
    "HUMAN",
    "MOUSE",
    "RAT",
    "AND",
    "OR",
    "WITH",
    "FOR",
    "IN",
    "ON",
}


def extract_gene_candidates(text: str) -> list[str]:
    if not text:
        return []
    raw_tokens = _GENE_TOKEN_RE.findall(text)
    candidates: list[str] = []
    for token in raw_tokens:
        upper = token.upper()
        # Heuristic: genes are typically short-ish and often all-caps.
        if len(upper) < 2 or len(upper) > 12:
            continue
        # Skip mutation shorthand tokens like G12C, Q61H, etc.
        if _MUTATION_TOKEN_RE.match(upper):
            continue
        if upper in _STOP_TOKENS:
            continue
        # Skip obvious non-gene numeric-only tokens.
        if upper.isdigit():
            continue
        # Prefer all-caps or mixed with digits (e.g., BRCA1, KRAS, TP53).
        if token.isupper() or any(ch.isdigit() for ch in token):
            candidates.append(upper)
    return _dedupe_keep_order(candidates)


def is_likely_drug_discovery_query(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    keywords = [
        "target",
        "gene",
        "mutation",
        "inhibitor",
        "dependency",
        "tractability",
        "druggability",
        "cancer",
        "tumor",
        "oncology",
        "therapy",
        "pharos",
        "depmap",
        "open targets",
        "literature",
        "clinical",
    ]
    return any(k in t for k in keywords)


@dataclass(frozen=True)
class _HeuristicDecision:
    confident: bool
    result: QueryInterpretationResult


class QueryInterpretationAgent:
    def __init__(self, model: str | None = None, temperature: float = 0.0) -> None:
        self.model: str = model or os.getenv("A4T_QUERY_INTERPRETATION_MODEL") or "gpt-5-mini"
        self.temperature = temperature

    def _heuristic(self, *, message: str, context: QueryInterpretationContext) -> _HeuristicDecision:
        urls = extract_urls(message)
        cleaned = _strip_urls(message)
        disease_id = extract_disease_id(cleaned) or context.active_disease
        candidates = extract_gene_candidates(cleaned)

        # If this is a follow-up and the user did not mention a gene, keep the current target.
        if context.mode == "followup" and not candidates and context.active_gene:
            return _HeuristicDecision(
                confident=True,
                result=QueryInterpretationResult(
                    in_scope=True,
                    gene_symbol=context.active_gene.strip().upper(),
                    disease_id=disease_id,
                    objective=cleaned or message.strip() or "Follow-up question",
                    detected_urls=urls,
                    target_switch_detected=False,
                    user_message_to_show_if_out_of_scope=_OUT_OF_SCOPE_MESSAGE,
                ),
            )

        if len(candidates) == 1:
            gene = candidates[0]
            active = (context.active_gene or "").strip().upper() if context.active_gene else None
            switch = bool(active and active != gene)
            return _HeuristicDecision(
                confident=True,
                result=QueryInterpretationResult(
                    in_scope=True,
                    gene_symbol=gene,
                    disease_id=disease_id,
                    objective=cleaned or message.strip() or f"Therapeutic target assessment for {gene}",
                    detected_urls=urls,
                    target_switch_detected=switch,
                    user_message_to_show_if_out_of_scope=_OUT_OF_SCOPE_MESSAGE,
                ),
            )

        # No confident gene: decide if in-scope at all. If not, do not call LLM.
        likely = is_likely_drug_discovery_query(cleaned)
        if not likely:
            return _HeuristicDecision(
                confident=True,
                result=QueryInterpretationResult(
                    in_scope=False,
                    gene_symbol=None,
                    disease_id=None,
                    objective=cleaned or message.strip() or "User query",
                    detected_urls=urls,
                    target_switch_detected=False,
                    user_message_to_show_if_out_of_scope=_OUT_OF_SCOPE_MESSAGE,
                ),
            )

        # Ambiguous / missing: allow LLM.
        return _HeuristicDecision(
            confident=False,
            result=QueryInterpretationResult(
                in_scope=True,
                gene_symbol=None,
                disease_id=disease_id,
                objective=cleaned or message.strip() or "Drug discovery query",
                detected_urls=urls,
                target_switch_detected=False,
                user_message_to_show_if_out_of_scope=_OUT_OF_SCOPE_MESSAGE,
            ),
        )

    async def interpret(self, *, message: str, context: QueryInterpretationContext) -> QueryInterpretationResult:
        message = (message or "").strip()
        if not message:
            return QueryInterpretationResult(
                in_scope=False,
                gene_symbol=None,
                disease_id=None,
                objective="(empty message)",
                detected_urls=[],
                target_switch_detected=False,
                user_message_to_show_if_out_of_scope=_OUT_OF_SCOPE_MESSAGE,
            )

        heur = self._heuristic(message=message, context=context)
        if heur.confident:
            return heur.result

        # LLM path (only when needed).
        if not llm_calls_enabled():
            if require_llm_agents():
                raise RuntimeError("LLM calls are disabled, but query interpretation requires LLM for this input.")
            return heur.result

        if not llm_configured():
            ensure_llm_available("query_interpretation_agent")
            if require_llm_agents():
                raise RuntimeError("LLM is not configured, but query interpretation requires LLM for this input.")
            return heur.result

        class _LLMInterpretation(BaseModel):
            in_scope: bool
            gene_symbol: str | None = None
            disease_id: str | None = None
            objective: str
            detected_urls: list[str] = Field(default_factory=list)
            target_switch_detected: bool = False
            user_message_to_show_if_out_of_scope: str = Field(default=_OUT_OF_SCOPE_MESSAGE)

        prompt = (
            "You are a drug discovery query interpretation agent.\n"
            "Task: classify whether the user message is in-scope for therapeutic target/drug discovery, "
            "and extract the target gene symbol and (optional) disease ID.\n"
            "Rules:\n"
            "- gene_symbol must be an uppercase HGNC-like symbol if present (e.g., KRAS, EGFR, TP53).\n"
            "- disease_id may be an ontology-like identifier (e.g., EFO_0003060, MONDO_0004992) if present.\n"
            "- If the user is out-of-scope, set in_scope=false and fill user_message_to_show_if_out_of_scope.\n"
            "- detected_urls must include URLs found in the message.\n"
            "- If mode=followup and the message clearly switches targets away from active_gene, set target_switch_detected=true.\n\n"
            f"Context: mode={context.mode}, active_gene={context.active_gene}, active_disease={context.active_disease}\n"
            f"User message: {message}\n"
        )

        runnable = structured_runnable(schema=_LLMInterpretation, model=self.model, temperature=self.temperature, method="function_calling")
        parsed = await runnable.ainvoke(prompt)
        # Normalize.
        gene = parsed.gene_symbol.strip().upper() if isinstance(parsed.gene_symbol, str) and parsed.gene_symbol.strip() else None
        disease = parsed.disease_id.strip() if isinstance(parsed.disease_id, str) and parsed.disease_id.strip() else None
        urls = _dedupe_keep_order([u.strip() for u in (parsed.detected_urls or []) if isinstance(u, str) and u.strip()])
        return QueryInterpretationResult(
            in_scope=bool(parsed.in_scope),
            gene_symbol=gene or context.active_gene,
            disease_id=disease or context.active_disease,
            objective=(parsed.objective or message).strip() or message,
            detected_urls=urls or extract_urls(message),
            target_switch_detected=bool(parsed.target_switch_detected) if context.mode == "followup" else False,
            user_message_to_show_if_out_of_scope=(parsed.user_message_to_show_if_out_of_scope or _OUT_OF_SCOPE_MESSAGE).strip() or _OUT_OF_SCOPE_MESSAGE,
        )
