"""Followup Q&A router."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from agents.artifact_store import artifact_layout
from agents.followup_agent import FollowupAgent, FollowupContext
from agents.query_interpretation_agent import QueryInterpretationAgent, QueryInterpretationContext
from agents.run_state_store import RunStateStore
from agents.schema import EvidenceDossier, EvidenceRecord
from agents.url_resource_fetcher import UrlResourceFetcher, extract_urls as extract_urls_from_text
from api.event_bus import BUS
from api.models import FollowupInput, FollowupResponse

router = APIRouter()


def _evidence_index_from_dossier(dossier: EvidenceDossier, *, max_items: int = 60) -> list[dict[str, Any]]:
    items = sorted(list(dossier.verified_evidence or []), key=lambda it: float(it.confidence or 0.0), reverse=True)
    _KEYS = ("rank","pmid","title","pub_year","cited_by_count","cell_line_id","gene_effect","tdl","family","ligand_total","tractability","association_count")
    return [{"evidence_id": it.evidence_id or f"{it.source}:{it.target_id}:{it.evidence_type}", "source": it.source, "type": it.evidence_type, "summary": it.summary, "confidence": it.confidence, "normalized_score": it.normalized_score, "raw_value": it.raw_value, "support": {k: v for k, v in (it.support if isinstance(it.support, dict) else {}).items() if k in _KEYS}} for it in items[:max_items]]


def _evidence_index_from_records(records: list[EvidenceRecord], *, max_items: int = 60) -> list[dict[str, Any]]:
    items = sorted(list(records or []), key=lambda it: float(it.confidence or 0.0), reverse=True)
    _KEYS = ("rank","pmid","title","pub_year","cited_by_count","cell_line_id","gene_effect","tdl","family","ligand_total","tractability","association_count")
    return [{"evidence_id": it.evidence_id or f"{it.source}:{it.target_id}:{it.evidence_type}", "source": it.source, "type": it.evidence_type, "summary": it.summary, "confidence": it.confidence, "normalized_score": it.normalized_score, "raw_value": it.raw_value, "support": {k: v for k, v in (it.support if isinstance(it.support, dict) else {}).items() if k in _KEYS}} for it in items[:max_items]]


@router.post("/api/runs/{run_id}/followup", response_model=FollowupResponse)
async def followup(run_id: str, body: FollowupInput) -> FollowupResponse:
    BUS.ensure_run(run_id)
    layout = artifact_layout(run_id)
    dossier_path = layout["dossier"]
    dossier: EvidenceDossier | None = None
    fallback_snapshot = None
    try:
        with open(dossier_path, "r") as f:
            dossier = EvidenceDossier.model_validate(json.load(f))
    except FileNotFoundError:
        fallback_snapshot = RunStateStore.load_latest(run_id)
        if fallback_snapshot is None:
            raise HTTPException(status_code=404, detail=f"Run dossier not found for run_id `{run_id}`")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load dossier for run_id `{run_id}`: {exc}")

    if dossier is not None:
        gene, disease, objective = dossier.query.gene_symbol, dossier.query.disease_id, dossier.query.objective
        model_override = dossier.query.model_override
        dossier_summary_markdown = dossier.summary_markdown or ""
        evidence_index = _evidence_index_from_dossier(dossier)
    else:
        values = (fallback_snapshot.values if fallback_snapshot else {}) or {}
        query = values.get("query") if isinstance(values.get("query"), dict) else {}
        gene = str(query.get("gene_symbol") or "").strip() or "UNKNOWN"
        disease = str(query.get("disease_id") or "").strip() or None
        objective = str(query.get("objective") or "").strip() or None
        model_override = query.get("model_override") if isinstance(query.get("model_override"), str) else None
        dossier_summary_markdown = str(values.get("explanation") or "").strip()
        records_raw = values.get("normalized_items") if isinstance(values.get("normalized_items"), list) else values.get("verified_evidence") if isinstance(values.get("verified_evidence"), list) else []
        try:
            records = [EvidenceRecord.model_validate(item) for item in records_raw]
        except Exception:
            records = []
        evidence_index = _evidence_index_from_records(records)
        if not dossier_summary_markdown or not evidence_index:
            raise HTTPException(status_code=409, detail="Follow-up is available after the report is generated. Please wait for the run to finish.")

    interp = QueryInterpretationAgent(model=model_override)
    parsed = await interp.interpret(message=body.message, context=QueryInterpretationContext(mode="followup", active_gene=gene, active_disease=disease))
    if not parsed.in_scope:
        return FollowupResponse(run_id=run_id, answer_markdown=parsed.user_message_to_show_if_out_of_scope, target_switch_detected=False, extracted_gene_symbol=None, used_urls=[])
    if parsed.target_switch_detected and parsed.gene_symbol:
        return FollowupResponse(run_id=run_id, answer_markdown=f"This run is for target **{gene}**. Your message looks like it switches targets to **{parsed.gene_symbol}**. Start a new thread/run for the new target.", target_switch_detected=True, extracted_gene_symbol=parsed.gene_symbol, used_urls=[])

    urls = list(dict.fromkeys([u.strip() for u in (parsed.detected_urls or []) + (body.urls or []) + extract_urls_from_text(body.message) if isinstance(u, str) and u.strip()]))
    BUS.publish(run_id, "followup_started", {"run_id": run_id})
    try:
        fetcher = UrlResourceFetcher()
        resources = await fetcher.fetch(urls)
        used_urls = [r.url for r in resources]
        context = FollowupContext(run_id=run_id, gene_symbol=gene, disease_id=disease, original_objective=objective, dossier_summary_markdown=dossier_summary_markdown, evidence_index=evidence_index, url_resources=[{"url": r.url, "content_type": r.content_type, "title": r.title, "text": r.text, "bytes_fetched": r.bytes_fetched} for r in resources])
        answer = await FollowupAgent(model=model_override).answer(question=body.message, context=context)
        BUS.publish(run_id, "followup_completed", {"run_id": run_id, "used_urls": used_urls})
        return FollowupResponse(run_id=run_id, answer_markdown=answer.answer_markdown, target_switch_detected=False, extracted_gene_symbol=gene, used_urls=used_urls)
    except Exception as exc:
        BUS.publish(run_id, "followup_failed", {"run_id": run_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"Follow-up failed: {exc}")
