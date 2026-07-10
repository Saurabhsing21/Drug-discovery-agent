from __future__ import annotations

import json
import os
from typing import Iterable

from drugagent.llm.policy import default_fast_model, ensure_llm_available, require_llm_agents
from drugagent.core.schema import (
    CollectorRequest,
    ConflictRecord,
    EvidenceRecord,
    EvidenceGraphSnapshot,
    LLMSummary,
    RobustnessSummary,
    SourceStatus,
    StatusName,
    VerificationReport,
)
from drugagent.scoring.schemas import ScoredTarget
import drugagent.llm.prompts as prompts
from drugagent.agents.summarizer_validation import validate_summary_markdown
from drugagent.memory.content import inject_content_memory
from drugagent.observability.prompt_trace import persist_prompt_trace
from drugagent.scoring.engine import ScoredDecision, category_for_evidence, record_quality, score_evidence
from drugagent.data.bio_context import fetch_uniprot_context


class SummaryAgent:
    """Generate a structured evidence summary using Google Gemini (default), with deterministic fallback."""

    def __init__(self, model: str | None = None, temperature: float = 0.7):
        self.model: str = model or os.getenv("A4T_SUMMARY_MODEL") or default_fast_model()
        self.temperature = temperature

    def _report_format(self) -> str:
        raw = os.getenv("A4T_REPORT_FORMAT", "structured").strip().lower()
        if raw == "concise":
            return "structured"
        return raw if raw in {"structured", "dossier", "compiler"} else "structured"

    @staticmethod
    def _dashboard_block(run_id: str, evidence_dashboard_path: str | None) -> str:
        return "\n".join(
            [
                "## Evidence Contribution Dashboard",
                "[[EVIDENCE_DASHBOARD]]",
            ]
        )

    @staticmethod
    def _header_block(gene_symbol: str, run_id: str | None) -> str:
        if run_id:
            return f"Gene: {gene_symbol} | Run ID: {run_id}"
        return f"Gene: {gene_symbol}"

    def _inject_header_and_dashboard(
        self,
        text: str,
        *,
        gene_symbol: str,
        run_id: str | None,
        evidence_dashboard_path: str | None,
    ) -> str:
        if "[[EVIDENCE_DASHBOARD]]" in text:
            return text
        header = self._header_block(gene_symbol, run_id)
        dashboard = self._dashboard_block(run_id or "unknown", evidence_dashboard_path) if run_id else ""
        title = "# THERAPEUTIC TARGET EVIDENCE SUMMARY REPORT"
        if title in text:
            before, after = text.split(title, 1)
            after = after.lstrip("\n")
            injected = f"{title}\n\n{header}\n\n{dashboard}\n\n{after}" if dashboard else f"{title}\n\n{header}\n\n{after}"
            return (before + injected).strip()
        injected = f"{header}\n\n{dashboard}\n\n{text}" if dashboard else f"{header}\n\n{text}"
        return injected.strip()

    @staticmethod
    def _enum_value(value: object) -> str:
        return value.value if hasattr(value, "value") else str(value)

    def _build_payload(
        self,
        request: CollectorRequest,
        items: list[EvidenceRecord],
        source_status: list[SourceStatus],
        verification_report: VerificationReport | None = None,
        conflicts: list[ConflictRecord] | None = None,
        evidence_graph: EvidenceGraphSnapshot | None = None,
        scored_target: ScoredTarget | None = None,
        evidence_dashboard_path: str | None = None,
    ) -> dict:
        coverage = [
            {
                "name": self._enum_value(s.source),
                "status": self._enum_value(s.status),
                "record_count": s.record_count,
                "error": s.error_message,
            }
            for s in source_status
        ]
        max_items = int(os.getenv("A4T_SUMMARY_PAYLOAD_MAX_ITEMS", "500") or "500")
        # Keep order stable but group by source/type to make the payload easier for an LLM to consume.
        sorted_items = sorted(
            items,
            key=lambda x: (
                self._enum_value(x.source),
                x.evidence_type or "",
                -(x.confidence or 0.0),
            ),
        )
        payload_items = sorted_items[:max_items]
        compact_items = [
            {
                "evidence_id": self._evidence_ref(item),
                "source": self._enum_value(item.source),
                "type": item.evidence_type,
                "summary": item.summary,
                "normalized_score": item.normalized_score,
                "confidence": item.confidence,
                "support": item.support,
            }
            for item in payload_items
        ]

        decision = score_evidence(
            request=request,
            items=items,
            conflicts=list(conflicts or []),
            verification_report=verification_report,
        )

        # Fetch biological context from UniProt (free, no key needed).
        # This gives the LLM protein class + curated function text so it can
        # explain WHY scores differ between targets, not just THAT they differ.
        biological_context = fetch_uniprot_context(request.gene_symbol)

        return {
            "target": request.gene_symbol,
            "disease_context": request.disease_id,
            "source_coverage": coverage,
            "verified_evidence": compact_items,
            "decision": decision.model_dump(mode="json"),
            "evidence_id_index": [self._evidence_ref(item) for item in sorted_items],
            "payload_truncation": (
                None
                if len(sorted_items) <= max_items
                else {
                    "max_items": max_items,
                    "total_items": len(sorted_items),
                    "omitted_evidence_ids": [self._evidence_ref(item) for item in sorted_items[max_items:]],
                }
            ),
            "verification_report": verification_report.model_dump(mode="json") if verification_report else None,
            "conflicts": [conflict.model_dump(mode="json") for conflict in (conflicts or [])],
            "graph_snapshot": (
                {
                    "node_count": len(evidence_graph.nodes),
                    "edge_count": len(evidence_graph.edges),
                    "artifact_path": evidence_graph.artifact_path,
                }
                if evidence_graph
                else None
            ),
            "evidence_dashboard": {
                "url": f"/api/runs/{request.run_id}/evidence-dashboard",
                "artifact_path": evidence_dashboard_path,
            }
            if request.run_id
            else None,
            "scored_target": scored_target.model_dump(mode="json") if scored_target else None,
            # Biological context enrichment from UniProt.
            # Fields: protein_full_name, protein_family, molecular_function,
            #         curated_disease_assocs, uniprot_accession
            # Empty dict if UniProt is unreachable (graceful degradation).
            "biological_context": biological_context,
        }

    def _evidence_ref(self, item: EvidenceRecord) -> str:
        return item.evidence_id or f"{self._enum_value(item.source)}:{item.target_id}:{item.disease_id or 'NA'}:{item.evidence_type}"

    def _coverage_summary(self, source_status: list[SourceStatus]) -> list[str]:
        lines: list[str] = []
        for status in source_status:
            source_name = self._enum_value(status.source)
            state = self._enum_value(status.status)
            extra = f", error={status.error_message}" if status.error_message else ""
            lines.append(
                f"{source_name}: status={state}, records={status.record_count}, duration_ms={status.duration_ms}{extra}"
            )
        return lines or ["No source execution records available."]

    @staticmethod
    def _score_bucket(confidence: float) -> str:
        if confidence >= 0.75:
            return "high"
        if confidence >= 0.5:
            return "medium"
        return "low"

    def _confidence_profile(self, items: list[EvidenceRecord]) -> list[str]:
        buckets = {"high": 0, "medium": 0, "low": 0}
        for item in items:
            buckets[self._score_bucket(item.confidence)] += 1
        return [
            f"high_confidence={buckets['high']}",
            f"medium_confidence={buckets['medium']}",
            f"low_confidence={buckets['low']}",
        ]

    def _conflict_notes(self, conflicts: list[ConflictRecord]) -> list[str]:
        if not conflicts:
            return ["No cross-source conflicts detected."]
        return [
            f"{conflict.severity}: {conflict.rationale} [refs: {', '.join(conflict.evidence_ids)}]"
            for conflict in conflicts
        ]

    def _grounded_findings(self, items: list[EvidenceRecord]) -> list[str]:
        findings: list[str] = []
        for item in sorted(items, key=lambda evidence: evidence.confidence, reverse=True)[:8]:
            findings.append(
                f"[{self._evidence_ref(item)}] {item.summary or item.evidence_type} "
                f"(source={self._enum_value(item.source)}, confidence={item.confidence:.3f}, "
                f"score={item.normalized_score if item.normalized_score is not None else 'n/a'})"
            )
        return findings or ["No verified evidence records available."]

    def _build_concise_report(
        self,
        *,
        request: CollectorRequest,
        decision: ScoredDecision,
        items: list[EvidenceRecord],
        source_status: list[SourceStatus],
        verification_report: VerificationReport | None,
        conflicts: list[ConflictRecord] | None,
    ) -> str:
        def _fmt(x: float | None) -> str:
            if x is None:
                return "n/a"
            return f"{x:.3f}"

        def _truncate(text: str, max_len: int) -> str:
            t = (text or "").strip()
            if len(t) <= max_len:
                return t
            return t[: max_len - 1].rstrip() + "…"

        lines: list[str] = []
        lines.append(f"# Target Evidence Report: {request.gene_symbol}")
        lines.append("")
        lines.append("## Executive Answer")
        disease_str = request.disease_id or "unspecified"
        lines.append(
            f"Decision: **{decision.decision_status}** "
            f"(overall_support_score={_fmt(decision.overall_support_score)}, "
            f"completeness={_fmt(decision.evidence_completeness_score)}). "
            f"Disease context: **{disease_str}**."
        )
        lines.append("")
        lines.append("## Evidence Summary")
        lines.append(self._compiler_table(
            ["category", "strength", "score", "top_evidence_id", "top_finding", "main_limitation"],
            [
                [
                    row.category,
                    row.strength,
                    _fmt(row.category_score),
                    row.top_evidence_id or "n/a",
                    row.top_finding or "n/a",
                    row.main_limitation or "n/a",
                ]
                for row in decision.summary_rows
            ],
        ))
        lines.append("")
        lines.append("## Top Evidence")
        top_rows: list[list[str]] = []
        for cat in ("annotation", "dependency", "disease_association", "literature"):
            cat_items = [it for it in items if category_for_evidence(it) == cat]
            for it in sorted(cat_items, key=record_quality, reverse=True)[:3]:
                top_rows.append(
                    [
                        cat,
                        self._evidence_ref(it),
                        self._enum_value(it.source),
                        _fmt(it.normalized_score),
                        _fmt(it.confidence),
                        _truncate((it.summary or it.evidence_type).replace("\n", " "), 120),
                    ]
                )
        if top_rows:
            lines.append(self._compiler_table(["category", "evidence_id", "source", "score", "confidence", "summary"], top_rows))
        else:
            lines.append("No evidence items available.")
        lines.append("")
        lines.append("## Source Coverage")
        coverage_rows: list[list[str]] = []
        for s in source_status:
            coverage_rows.append(
                [
                    self._enum_value(s.source),
                    self._enum_value(s.status),
                    str(s.record_count),
                    str(s.duration_ms),
                    (s.error_message or ""),
                ]
            )
        lines.append(self._compiler_table(["source", "status", "records", "duration_ms", "error"], coverage_rows))
        lines.append("")
        lines.append("## Conflicts & Caveats")
        if not conflicts:
            lines.append("- No cross-source conflicts detected.")
        else:
            for c in conflicts:
                refs = ", ".join(c.evidence_ids[:6]) + ("…" if len(c.evidence_ids) > 6 else "")
                lines.append(f"- {c.severity}: {c.rationale} (refs: {refs})")
        if verification_report is not None and verification_report.warning_issues:
            lines.append(f"- Verification warnings: {', '.join(verification_report.warning_issues)}.")
        if verification_report is not None and verification_report.blocked:
            lines.append(f"- Verification blocked: {', '.join(verification_report.blocking_issues)}.")
        lines.append("")
        lines.append("## Recommended Next Steps")
        for step in decision.recommended_next_steps:
            lines.append(f"- {step}")
        if not decision.recommended_next_steps:
            lines.append("- No specific next steps generated.")
        lines.append("")
        lines.append("## Machine Appendix")
        appendix_rows: list[list[str]] = []
        for it in sorted(items, key=lambda x: (self._enum_value(x.source), x.evidence_type, -(x.confidence or 0.0))):
            appendix_rows.append(
                [
                    self._evidence_ref(it),
                    self._enum_value(it.source),
                    it.evidence_type,
                    _fmt(it.normalized_score),
                    _fmt(it.confidence),
                    (it.summary or "").replace("\n", " ").strip(),
                ]
            )
        lines.append(self._compiler_table(["evidence_id", "source", "type", "normalized_score", "confidence", "summary"], appendix_rows))
        return "\n".join(lines).strip() + "\n"

    def _build_robustness(
        self,
        source_status: list[SourceStatus],
        item_count: int = 0,
    ) -> RobustnessSummary:
        requested_source_count = len(source_status)
        successful_source_count = 0
        failed_or_skipped_sources: list[str] = []
        source_record_depth: dict[str, int] = {}

        # Sources that naturally only produce 1 consolidated record
        SHALLOW_SOURCES = {"depmap", "literature", "pharos"}

        for s in source_status:
            status_name = self._enum_value(s.status)
            source_name = self._enum_value(s.source)
            source_record_depth[source_name] = int(s.record_count)
            if status_name == StatusName.SUCCESS.value:
                successful_source_count += 1
            elif status_name in {StatusName.FAILED.value, StatusName.SKIPPED.value, StatusName.PARTIAL.value}:
                failed_or_skipped_sources.append(source_name)

        used_evidence_count = item_count
        
        # Sane depth check: Successful sources must have >= 2 records UNLESS they are shallow sources
        def _check_depth(s):
            name = self._enum_value(s.source)
            depth = source_record_depth.get(name, 0)
            required = 1 if name in SHALLOW_SOURCES else 2
            ok = depth >= required
            return ok

        sufficient_depth = all(
            _check_depth(s)
            for s in source_status
            if self._enum_value(s.status) == StatusName.SUCCESS.value
        )
        
        minimum_coverage_met = successful_source_count >= 3 and used_evidence_count >= 4 and sufficient_depth

        verdict = (
            "sufficient_multi_source_evidence"
            if minimum_coverage_met
            else "insufficient_evidence_for_strong_claim"
        )

        return RobustnessSummary(
            requested_source_count=requested_source_count,
            successful_source_count=successful_source_count,
            used_evidence_count=used_evidence_count,
            source_record_depth=source_record_depth,
            minimum_coverage_met=minimum_coverage_met,
            failed_or_skipped_sources=failed_or_skipped_sources,
            verdict=verdict,
        )

    def _build_markdown_report(
        self,
        request: CollectorRequest,
        robustness: RobustnessSummary,
        coverage_lines: list[str],
        confidence_profile: list[str],
        conflict_notes: list[str],
        grounded_findings: list[str],
        verification_report: VerificationReport | None = None,
        evidence_graph: EvidenceGraphSnapshot | None = None,
        evidence_dashboard_path: str | None = None,
    ) -> str:
        success = robustness.successful_source_count
        total = robustness.requested_source_count

        lines = [
            f"# Target Evidence Summary: {request.gene_symbol}",
            "",
            "## Executive",
            (
                f"Evaluation completed across **{success}/{total}** sources. "
                "Aggregate scoring is disabled in raw MCP mode."
            ),
            (
                "Robustness verdict: "
                f"**{robustness.verdict}** "
                f"(coverage_met={robustness.minimum_coverage_met})."
            ),
            (
                f"Verification blocked: **{verification_report.blocked if verification_report else False}**; "
                f"warnings={verification_report.warning_count if verification_report else 0}; "
                f"conflicts={len(conflict_notes) if conflict_notes != ['No cross-source conflicts detected.'] else 0}."
            ),
            "",
            "## Source Coverage",
        ]
        for finding in coverage_lines:
            lines.append(f"- {finding}")

        lines.extend(["", "## Confidence Profile"])
        for row in confidence_profile:
            lines.append(f"- {row}")

        lines.extend(["", "## Conflict Notes"])
        for row in conflict_notes:
            lines.append(f"- {row}")

        lines.extend(["", "## Grounded Findings"])
        for finding in grounded_findings:
            lines.append(f"- {finding}")

        if evidence_graph is not None:
            lines.extend(
                [
                    "",
                    "## Graph Context",
                    f"- nodes={len(evidence_graph.nodes)}",
                    f"- edges={len(evidence_graph.edges)}",
                    f"- artifact={evidence_graph.artifact_path or 'not_persisted'}",
                ]
            )

        if request.run_id:
            lines.extend(["", self._dashboard_block(request.run_id, evidence_dashboard_path)])

        return "\n".join(lines)

    def _compiler_table(self, headers: list[str], rows: list[list[str]], include_index: bool = True) -> str:
        # Markdown table renderer (keeps output deterministic and UI-friendly).
        def _cell(text: str) -> str:
            # Escape table-breaking characters.
            return (text or "").replace("\n", " ").replace("|", "\\|").strip()

        final_headers = ["#"] + headers if include_index else headers
        safe_headers = [_cell(h) for h in final_headers]
        out = ["| " + " | ".join(safe_headers) + " |", "| " + " | ".join(["---"] * len(safe_headers)) + " |"]
        for i, row in enumerate(rows, 1):
            row_to_process = [str(i)] + row if include_index else row
            cells = [_cell(cell) for cell in row_to_process]
            # Pad/truncate to header length.
            if len(cells) < len(safe_headers):
                cells.extend([""] * (len(safe_headers) - len(cells)))
            out.append("| " + " | ".join(cells[: len(safe_headers)]) + " |")
        return "\n".join(out)

    def _build_compiler_report(
        self,
        request: CollectorRequest,
        items: list[EvidenceRecord],
        source_status: list[SourceStatus],
        verification_report: VerificationReport | None = None,
        conflicts: list[ConflictRecord] | None = None,
        evidence_dashboard_path: str | None = None,
    ) -> str:
        def ev_id(item: EvidenceRecord) -> str:
            return self._evidence_ref(item)

        # Partition evidence.
        dep_global = [it for it in items if "genetic_dependency" == it.evidence_type]
        dep_lines = [it for it in items if "genetic_dependency_cell_line" == it.evidence_type]
        disease_assoc = [it for it in items if "disease_association" == it.evidence_type]
        target_annot = [it for it in items if "target_annotation" == it.evidence_type]
        literature = [it for it in items if it.evidence_type == "literature_article"]

        # Basic coverage table from execution status.
        coverage_rows: list[list[str]] = []
        for status in source_status:
            coverage_rows.append(
                [
                    self._enum_value(status.source),
                    self._enum_value(status.status),
                    str(int(status.record_count)),
                    str(int(status.duration_ms)),
                    (status.error_message or ""),
                ]
            )

        def _fmt(x: object) -> str:
            if x is None:
                return ""
            if isinstance(x, float):
                # Preserve precision while keeping deterministic output compact.
                return f"{x:.6g}"
            return str(x)

        # Annotation table (expanded, to avoid losing useful fields in `support`).
        annot_rows: list[list[str]] = []
        for it in target_annot:
            support = it.support if isinstance(it.support, dict) else {}
            annot_rows.append(
                [
                    f"[{ev_id(it)}]",
                    self._enum_value(it.source),
                    _fmt(support.get("tdl")),
                    _fmt(support.get("family")),
                    _fmt(support.get("ligand_total")),
                    _fmt(support.get("novelty")),
                    _fmt(support.get("disease_association_count")),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        # Dependency global metrics table (expanded).
        dep_global_rows: list[list[str]] = []
        for it in dep_global:
            support = it.support if isinstance(it.support, dict) else {}
            avg_gene_effect = it.raw_value if isinstance(it.raw_value, (int, float)) else support.get("average_gene_effect")
            cell_line_count = support.get("cell_line_count")
            strong_count = support.get("strong_dependency_count")
            strong_fraction = support.get("strong_dependency_fraction")
            data_release = support.get("data_release")
            dep_global_rows.append(
                [
                    f"[{ev_id(it)}]",
                    self._enum_value(it.source),
                    _fmt(avg_gene_effect),
                    _fmt(cell_line_count),
                    _fmt(strong_count),
                    _fmt(strong_fraction),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    _fmt(data_release),
                    (it.summary or ""),
                ]
            )

        # Top dependent cell lines table (ranked by confidence, then score).
        dep_line_rows: list[list[str]] = []
        for it in sorted(dep_lines, key=lambda x: (x.confidence, x.normalized_score or 0.0), reverse=True)[:25]:
            support = it.support if isinstance(it.support, dict) else {}
            cell_line_id = str(support.get("cell_line_id") or it.target_id.split(":")[-1])
            gene_effect_val = (
                support.get("gene_effect")
                if support.get("gene_effect") is not None
                else (it.raw_value if isinstance(it.raw_value, (int, float)) else None)
            )
            rank = support.get("rank_within_gene") or support.get("rank")
            dep_line_rows.append(
                [
                    f"[{ev_id(it)}]",
                    cell_line_id,
                    _fmt(gene_effect_val),
                    _fmt(rank),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        # Disease association table.
        assoc_rows: list[list[str]] = []
        for it in disease_assoc:
            support = it.support if isinstance(it.support, dict) else {}
            score = it.raw_value if isinstance(it.raw_value, (int, float)) else it.normalized_score
            disease_name = support.get("disease_name")
            rank = support.get("rank")
            assoc_count = support.get("association_count") or support.get("evidence_count")
            trac = support.get("tractability") if isinstance(support.get("tractability"), dict) else None
            trac_str = ""
            if isinstance(trac, dict) and trac:
                # Keep compact and deterministic.
                trac_str = "; ".join([f"{k}={v}" for k, v in sorted(trac.items())])
            assoc_rows.append(
                [
                    f"[{ev_id(it)}]",
                    self._enum_value(it.source),
                    _fmt(disease_name),
                    (it.disease_id or ""),
                    _fmt(score),
                    _fmt(rank),
                    _fmt(assoc_count),
                    trac_str,
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        # Literature table.
        lit_rows: list[list[str]] = []
        for it in literature:
            support = it.support if isinstance(it.support, dict) else {}
            pmid = str(support.get("pmid") or support.get("PMID") or "")
            title = str(support.get("title") or "")
            year = support.get("pub_year")
            citations = support.get("cited_by_count")
            gene_in_title = support.get("gene_in_title")
            lit_rows.append(
                [
                    f"[{ev_id(it)}]",
                    pmid,
                    _fmt(year),
                    _fmt(citations),
                    _fmt(gene_in_title),
                    title,
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        conflict_notes = conflicts or []
        conflict_text = (
            "\n".join([f"- {c.severity}: {c.rationale} (refs={', '.join(c.evidence_ids)})" for c in conflict_notes])
            if conflict_notes
            else "- No conflicts detected."
        )
        verification_text = ""
        if verification_report is not None:
            verification_text = (
                f"Verification blocked={verification_report.blocked}; "
                f"blocking_issues={len(verification_report.blocking_issues)}; "
                f"warnings={len(verification_report.warning_issues)}."
            )

        # Narratives (deterministic, grounded in available fields only).
        annot_ref = f"[{ev_id(target_annot[0])}]" if target_annot else ""
        dep_ref = f"[{ev_id(dep_global[0])}]" if dep_global else ""
        ot_ref = f"[{ev_id(disease_assoc[0])}]" if disease_assoc else ""
        lit_ref = f"[{ev_id(literature[0])}]" if literature else ""

        annot_support = (target_annot[0].support if target_annot and isinstance(target_annot[0].support, dict) else {}) if target_annot else {}
        dep_support = (dep_global[0].support if dep_global and isinstance(dep_global[0].support, dict) else {}) if dep_global else {}

        # Top diseases (names + scores) from collected association records.
        top_disease_summaries: list[str] = []
        for it in sorted(disease_assoc, key=lambda x: float(x.normalized_score or 0.0), reverse=True)[:3]:
            sup = it.support if isinstance(it.support, dict) else {}
            nm = sup.get("disease_name") or it.disease_id or "unknown_disease"
            sc = it.raw_value if isinstance(it.raw_value, (int, float)) else it.normalized_score
            top_disease_summaries.append(f"{nm} (score={_fmt(sc)})")

        # Top dependent cell lines (ids + effects).
        top_dep_summaries: list[str] = []
        def _dep_effect(it: EvidenceRecord) -> float:
            if isinstance(it.raw_value, (int, float)):
                return float(it.raw_value)
            sup = it.support if isinstance(it.support, dict) else {}
            ge = sup.get("gene_effect")
            if isinstance(ge, (int, float)):
                return float(ge)
            return 0.0

        for it in sorted(dep_lines, key=_dep_effect)[:3]:
            sup = it.support if isinstance(it.support, dict) else {}
            cl = sup.get("cell_line_id") or it.target_id.split(":")[-1]
            eff = sup.get("gene_effect") if sup.get("gene_effect") is not None else it.raw_value
            top_dep_summaries.append(f"{cl} (gene_effect={_fmt(eff)})")

        # Build the 9-section compiler report.
        lines: list[str] = []
        lines.append("# THERAPEUTIC TARGET EVIDENCE SUMMARY REPORT")
        lines.append("")
        lines.append(self._header_block(request.gene_symbol, request.run_id))
        lines.append("")
        if request.run_id:
            lines.append(self._dashboard_block(request.run_id, evidence_dashboard_path))
            lines.append("")
        lines.append("## 1. Executive Summary")
        lines.append("")
        lines.append(
            (
                f"Target: {request.gene_symbol}. "
                + (f"Disease context: {request.disease_id}. " if request.disease_id else "Disease context: not specified. ")
                + f"Sources executed: {', '.join([self._enum_value(s.source) for s in source_status]) or 'none'}. "
                + (verification_text + " " if verification_text else "")
            ).strip()
        )
        lines.append("")
        if target_annot:
            lines.append(
                f"Target annotation {annot_ref} reports TDL={_fmt(annot_support.get('tdl'))}, "
                f"family={_fmt(annot_support.get('family'))}, ligands={_fmt(annot_support.get('ligand_total'))}, "
                f"novelty={_fmt(annot_support.get('novelty'))}. "
                "These fields reflect target development maturity and tractability as provided by the source."
            )
            lines.append("")
        if dep_global:
            lines.append(
                f"Genetic dependency evidence {dep_ref} summarizes DepMap CRISPR gene effect across "
                f"{_fmt(dep_support.get('cell_line_count'))} cell lines with average effect={_fmt(dep_global[0].raw_value)} "
                f"and strong_dependency_count={_fmt(dep_support.get('strong_dependency_count'))} "
                f"(fraction={_fmt(dep_support.get('strong_dependency_fraction'))})."
            )
            if top_dep_summaries:
                lines.append(f"Most dependent cell lines (from compiled records): {', '.join(top_dep_summaries)}.")
            lines.append("")
        if disease_assoc:
            lines.append(
                f"Disease association evidence {ot_ref} includes {len(disease_assoc)} compiled associations. "
                + (f"Top scored examples: {', '.join(top_disease_summaries)}." if top_disease_summaries else "")
            )
            lines.append("")
        if literature:
            lines.append(
                f"Literature evidence {lit_ref} includes {len(literature)} Europe PMC items selected from a larger hit set "
                "and ranked to emphasize target-in-title relevance while retaining citation impact."
            )
        lines.append("")
        lines.append("")
        lines.append("## 2. Target Annotation — PHAROS")
        lines.append("")
        lines.append("This section compiles target annotation evidence as provided by the evidence payload.")
        if target_annot:
            lines.append(
                f"Key extracted fields include Target Development Level (TDL), ligand counts, and novelty {annot_ref}. "
                "Interpretation is limited to the values present in the support payload."
            )
        lines.append("")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "source",
                    "tdl",
                    "family",
                    "ligand_total",
                    "novelty",
                    "disease_association_count",
                    "confidence",
                    "normalized_score",
                    "summary",
                ],
                annot_rows or [["", "", "", "", "", "", "", "", "", "No target annotation evidence provided."]],
            )
        )
        lines.append("")

        lines.append("## 3. Genetic Dependency — DepMap")
        lines.append("")
        lines.append("### Global Dependency Analysis")
        lines.append("")
        lines.append("This subsection compiles global genetic dependency signals across screened cell lines when present.")
        if dep_global:
            lines.append(
                f"Global dependency metrics {dep_ref} summarize average gene effect and the fraction of strongly dependent cell lines "
                "using the provider's thresholding, as reported in the support payload."
            )
        lines.append("")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "source",
                    "average_gene_effect",
                    "cell_line_count",
                    "strong_dependency_count",
                    "strong_dependency_fraction",
                    "confidence",
                    "normalized_score",
                    "data_release",
                    "summary",
                ],
                dep_global_rows or [["", "", "", "", "", "", "", "", "", "No global dependency metric provided."]],
            )
        )
        lines.append("")
        lines.append("### Top Dependent Cell Lines")
        lines.append("")
        lines.append("This subsection compiles the top dependent cell-line records (ranked) when present.")
        lines.append("")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "cell_line_id",
                    "gene_effect",
                    "rank_within_gene",
                    "confidence",
                    "normalized_score",
                    "summary",
                ],
                dep_line_rows or [["", "", "", "", "", "", "No cell-line dependency evidence provided."]],
            )
        )
        lines.append("")

        lines.append("## 4. Disease Associations — Open Targets")
        lines.append("")
        lines.append("This section compiles disease association evidence (gene-to-disease links) provided by the payload.")
        if not request.disease_id:
            lines.append(
                "Disease context was not specified in the request, so the compiled associations reflect the source's global ranking; "
                "for indication-specific reporting, provide an explicit disease identifier in the query."
            )
        lines.append("")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "source",
                    "disease_name",
                    "disease_id",
                    "score",
                    "rank",
                    "association_count",
                    "tractability",
                    "confidence",
                    "normalized_score",
                    "summary",
                ],
                assoc_rows or [["", "", "", "", "", "", "", "", "", "", "No disease association evidence provided."]],
            )
        )
        lines.append("")

        lines.append("## 5. Literature")
        lines.append("")
        lines.append("This section compiles literature-derived evidence items as provided by the payload.")
        lines.append(
            "Items are selected from Europe PMC and locally re-ranked to prioritize papers where the target symbol appears in the title; "
            "citation counts are retained as an impact signal."
        )
        lines.append("")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "pmid",
                    "pub_year",
                    "cited_by_count",
                    "gene_in_title",
                    "title",
                    "confidence",
                    "normalized_score",
                    "summary",
                ],
                lit_rows or [["", "", "", "", "", "No literature evidence provided.", "", "", ""]],
            )
        )
        lines.append("")

        lines.append("## 6. Integrated Interpretation")
        lines.append("")
        lines.append(
            "Integrated interpretation connects evidence categories strictly as present in the payload. "
            "No external claims are introduced beyond the compiled records."
        )
        lines.append("")
        lines.append(
            f"Available categories: annotation={len(target_annot)}, dependency_global={len(dep_global)}, "
            f"dependency_cell_line={len(dep_lines)}, disease_association={len(disease_assoc)}, literature={len(literature)}."
        )
        lines.append("")
        if target_annot and dep_global:
            lines.append(
                f"Annotation {annot_ref} provides development-level and ligand context for {request.gene_symbol}, "
                f"while dependency evidence {dep_ref} provides functional sensitivity patterns across screened models. "
                "Together these supply complementary views: tractability/maturity vs. experimental dependency."
            )
            lines.append("")
        if disease_assoc:
            lines.append(
                "Disease association records contextualize where the target is linked to phenotypes/diseases in the compiled payload; "
                "these can be compared against functional dependency signals when the same disease context is specified."
            )
            lines.append("")
        if literature:
            lines.append(
                "Literature items provide high-level bibliographic anchors; the set is influenced by the ranking/selection policy "
                "and should be interpreted as representative rather than exhaustive."
            )
        lines.append("")
        lines.append("## 7. Evidence Strength Assessment")
        lines.append("")
        lines.append("Category-by-category assessment based only on evidence availability, quantity, and internal consistency.")
        lines.append("")
        lines.append(
            f"Target annotation evidence count: {len(target_annot)}. "
            f"Genetic dependency evidence count: global={len(dep_global)}, cell_line={len(dep_lines)}. "
            f"Disease association evidence count: {len(disease_assoc)}. "
            f"Literature evidence count: {len(literature)}."
        )
        lines.append(
            "Strength is higher when multiple categories are present and internally consistent; "
            "limitations include per-source top-k truncation and missing/unspecified disease context when applicable."
        )
        lines.append("")
        lines.append("Conflicts summary:")
        lines.append(conflict_text)
        lines.append("")

        lines.append("## 8. Overall Assessment")
        lines.append("")
        lines.append(
            "Overall target assessment is a synthesis grounded in the compiled tables. "
            "It reflects evidence presence and consistency rather than an external efficacy claim."
        )
        if target_annot or dep_global or disease_assoc:
            lines.append(
                f"For {request.gene_symbol}, the compiled payload supports a multi-source view spanning "
                f"annotation ({len(target_annot)}), functional dependency ({len(dep_global) + len(dep_lines)}), "
                f"and disease association ({len(disease_assoc)}) evidence records."
            )
        lines.append("")

        lines.append("## 9. Final Conclusion")
        lines.append("")
        lines.append(
            "Conclusion is limited to what is supported by the compiled evidence categories above. "
            "If the report feels sparse for a given target, increasing `per_source_top_k` and providing a specific disease context "
            "typically yields a more indication-focused evidence set without discarding collected data."
        )
        lines.append("")

        return "\n".join(lines)

    def _build_compiler_tables_appendix(
        self,
        *,
        request: CollectorRequest,
        items: list[EvidenceRecord],
        source_status: list[SourceStatus],
        evidence_dashboard_path: str | None = None,
    ) -> str:
        """Appendix with machine-compiled Markdown tables for UI readability.

        This is intentionally deterministic so the UI always gets valid Markdown tables
        even when the main report is produced by an LLM.
        """

        def ev_id(item: EvidenceRecord) -> str:
            return self._evidence_ref(item)

        def _fmt(x: object) -> str:
            if x is None:
                return ""
            if isinstance(x, float):
                return f"{x:.6g}"
            return str(x)

        dep_global = [it for it in items if "genetic_dependency" == it.evidence_type]
        dep_lines = [it for it in items if "genetic_dependency_cell_line" == it.evidence_type]
        disease_assoc = [it for it in items if "disease_association" == it.evidence_type]
        target_annot = [it for it in items if "target_annotation" == it.evidence_type]
        literature = [it for it in items if it.evidence_type == "literature_article"]

        coverage_rows: list[list[str]] = []
        for status in source_status:
            coverage_rows.append(
                [
                    self._enum_value(status.source),
                    self._enum_value(status.status),
                    str(int(status.record_count)),
                    str(int(status.duration_ms)),
                    (status.error_message or ""),
                ]
            )

        annot_rows: list[list[str]] = []
        for it in target_annot:
            support = it.support if isinstance(it.support, dict) else {}
            annot_rows.append(
                [
                    ev_id(it),
                    self._enum_value(it.source),
                    _fmt(support.get("tdl")),
                    _fmt(support.get("family")),
                    _fmt(support.get("ligand_total")),
                    _fmt(support.get("novelty")),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        dep_global_rows: list[list[str]] = []
        for it in dep_global:
            support = it.support if isinstance(it.support, dict) else {}
            avg_gene_effect = it.raw_value if isinstance(it.raw_value, (int, float)) else support.get("average_gene_effect")
            dep_global_rows.append(
                [
                    ev_id(it),
                    self._enum_value(it.source),
                    _fmt(avg_gene_effect),
                    _fmt(support.get("cell_line_count")),
                    _fmt(support.get("strong_dependency_count")),
                    _fmt(support.get("strong_dependency_fraction")),
                    _fmt(support.get("data_release")),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        dep_line_rows: list[list[str]] = []
        for it in sorted(dep_lines, key=lambda x: (x.confidence, x.normalized_score or 0.0), reverse=True)[:25]:
            support = it.support if isinstance(it.support, dict) else {}
            cell_line_id = str(support.get("cell_line_id") or it.target_id.split(":")[-1])
            gene_effect_val = (
                support.get("gene_effect")
                if support.get("gene_effect") is not None
                else (it.raw_value if isinstance(it.raw_value, (int, float)) else None)
            )
            rank = support.get("rank_within_gene") or support.get("rank")
            dep_line_rows.append(
                [
                    ev_id(it),
                    cell_line_id,
                    _fmt(gene_effect_val),
                    _fmt(rank),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        assoc_rows: list[list[str]] = []
        for it in sorted(disease_assoc, key=lambda x: float(x.normalized_score or 0.0), reverse=True)[:25]:
            support = it.support if isinstance(it.support, dict) else {}
            score = it.raw_value if isinstance(it.raw_value, (int, float)) else it.normalized_score
            assoc_rows.append(
                [
                    ev_id(it),
                    self._enum_value(it.source),
                    _fmt(support.get("disease_name")),
                    (it.disease_id or ""),
                    _fmt(score),
                    _fmt(support.get("evidence_count") or support.get("association_count")),
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                    (it.summary or ""),
                ]
            )

        lit_rows: list[list[str]] = []
        for it in sorted(literature, key=lambda x: float((x.support or {}).get("cited_by_count") or 0), reverse=True)[:25]:
            support = it.support if isinstance(it.support, dict) else {}
            pmid = str(support.get("pmid") or support.get("PMID") or "")
            title = str(support.get("title") or "")
            year = support.get("pub_year")
            citations = support.get("cited_by_count")
            lit_rows.append(
                [
                    ev_id(it),
                    pmid,
                    _fmt(year),
                    _fmt(citations),
                    title,
                    f"{it.confidence:.3f}",
                    _fmt(it.normalized_score),
                ]
            )

        lines: list[str] = []
        lines.append("# Appendix A — Raw Evidence Tables")
        lines.append("")
        lines.append(
            "These tables are compiled directly from the verified evidence payload to make the report scannable.\n"
            "- `evidence_id` is the canonical identifier used for traceability.\n"
            "- `normalized_score` and `confidence` are copied from the payload (they are not re-computed here).\n"
            "- If the narrative report above contains non-table lists, use these tables as the authoritative tabular view."
        )
        lines.append("")

        lines.append("## A1. Source Coverage")
        lines.append(self._compiler_table(["source", "status", "records", "duration_ms", "error"], coverage_rows))
        lines.append("")
        lines.append("## A2. Target Annotation (PHAROS)")
        lines.append(
            self._compiler_table(
                ["evidence_id", "source", "tdl", "family", "ligand_total", "novelty", "confidence", "normalized_score", "summary"],
                annot_rows or [["", "", "", "", "", "", "", "", "No target annotation evidence present."]],
            )
        )
        lines.append("")

        lines.append("## A3. Genetic Dependency (DepMap)")
        lines.append("### A3.1 Global metrics")
        lines.append(
            self._compiler_table(
                [
                    "evidence_id",
                    "source",
                    "average_gene_effect",
                    "cell_line_count",
                    "strong_dependency_count",
                    "strong_dependency_fraction",
                    "data_release",
                    "confidence",
                    "normalized_score",
                    "summary",
                ],
                dep_global_rows or [["", "", "", "", "", "", "", "", "", "No global dependency metric present."]],
            )
        )
        lines.append("")
        lines.append("### A3.2 Top dependent cell lines")
        lines.append(
            self._compiler_table(
                ["evidence_id", "cell_line_id", "gene_effect", "rank_within_gene", "confidence", "normalized_score", "summary"],
                dep_line_rows or [["", "", "", "", "", "", "No cell-line dependency evidence present."]],
            )
        )
        lines.append("")

        lines.append("## A4. Disease Associations (Open Targets)")
        lines.append(
            self._compiler_table(
                ["evidence_id", "source", "disease_name", "disease_id", "score", "evidence_count", "confidence", "normalized_score", "summary"],
                assoc_rows or [["", "", "", "", "", "", "", "", "No disease association evidence present."]],
            )
        )
        lines.append("")

        lines.append("## A5. Literature (Europe PMC)")
        lines.append(
            self._compiler_table(
                ["evidence_id", "pmid", "year", "cited_by", "title", "confidence", "normalized_score"],
                lit_rows or [["", "", "", "", "No literature evidence present.", "", ""]],
            )
        )
        lines.append("")

        return "\n".join(lines)

    def _deterministic_fallback(
        self,
        request: CollectorRequest,
        items: list[EvidenceRecord],
        source_status: list[SourceStatus],
        verification_report: VerificationReport | None = None,
        conflicts: list[ConflictRecord] | None = None,
        evidence_graph: EvidenceGraphSnapshot | None = None,
        evidence_dashboard_path: str | None = None,
    ) -> LLMSummary:
        robustness = self._build_robustness(source_status, item_count=len(items))

        if self._report_format() == "structured":
            markdown_report = self._build_compiler_report(
                request=request,
                items=items,
                source_status=source_status,
                verification_report=verification_report,
                conflicts=conflicts,
                evidence_dashboard_path=evidence_dashboard_path,
            )
        else:
            markdown_report = self._build_markdown_report(
                request=request,
                robustness=robustness,
                coverage_lines=self._coverage_summary(source_status),
                confidence_profile=self._confidence_profile(items),
                conflict_notes=self._conflict_notes(conflicts or []),
                grounded_findings=self._grounded_findings(items),
                verification_report=verification_report,
                evidence_graph=evidence_graph,
                evidence_dashboard_path=evidence_dashboard_path,
            )

        return LLMSummary(
            markdown_report=markdown_report,
            robustness=robustness,
            model_used=None,
            generation_mode="deterministic_fallback",
        )

    async def run(
        self,
        request: CollectorRequest,
        items: Iterable[EvidenceRecord],
        source_status: Iterable[SourceStatus],
        verification_report: VerificationReport | None = None,
        conflicts: list[ConflictRecord] | None = None,
        evidence_graph: EvidenceGraphSnapshot | None = None,
        scored_target: ScoredTarget | None = None,
        evidence_dashboard_path: str | None = None,
    ) -> LLMSummary:
        item_list = list(items)
        status_list = list(source_status)

        fmt = self._report_format()

        # Default "structured" report is deterministic and does not require an LLM.
        if fmt == "structured":
            decision = score_evidence(
                request=request,
                items=item_list,
                conflicts=list(conflicts or []),
                verification_report=verification_report,
            )
            markdown_report = self._build_concise_report(
                request=request,
                decision=decision,
                items=item_list,
                source_status=status_list,
                verification_report=verification_report,
                conflicts=conflicts,
            )
            if request.run_id:
                markdown_report += "\n\n" + self._dashboard_block(request.run_id, evidence_dashboard_path)
            summary = LLMSummary(markdown_report=markdown_report, generation_mode="deterministic_scored")
            summary.robustness = self._build_robustness(status_list, item_count=len(item_list))
            summary.model_used = None
            return summary

        from drugagent.llm.policy import llm_calls_enabled, llm_configured

        if fmt == "compiler":
            if not llm_calls_enabled():
                from drugagent.llm.providers import current_provider_selection
                sel = current_provider_selection()
                details = f" Probe error: {sel.error}" if sel and sel.error else ""
                raise RuntimeError(
                    f"Compiler report requires LLM calls enabled.{details} "
                    "Ensure A4T_LLM_CALLS_ENABLED is not 0 and your API keys are valid."
                )
            if not llm_configured():
                raise RuntimeError(
                    "Compiler report requires a valid LLM API key. "
                    "Set OPENAI_API_KEY or GOOGLE_API_KEY (or GEMINI_API_KEY) and retry."
                )

        if not llm_calls_enabled():
            if require_llm_agents():
                raise RuntimeError("LLM calls are disabled (A4T_LLM_CALLS_ENABLED=0) but A4T_REQUIRE_LLM_AGENTS=1.")
            return self._deterministic_fallback(
                request,
                item_list,
                status_list,
                verification_report=verification_report,
                conflicts=conflicts,
                evidence_graph=evidence_graph,
            )

        if not llm_configured():
            ensure_llm_available("summary_agent")
            return self._deterministic_fallback(
                request,
                item_list,
                status_list,
                verification_report=verification_report,
                conflicts=conflicts,
                evidence_graph=evidence_graph,
            )

        payload = self._build_payload(
            request,
            item_list,
            status_list,
            verification_report=verification_report,
            conflicts=conflicts,
            evidence_graph=evidence_graph,
            scored_target=scored_target,
            evidence_dashboard_path=evidence_dashboard_path,
        )

        try:
            from drugagent.llm.policy import ainvoke_with_fallbacks

            if fmt == "dossier":
                system_prompt = prompts.get_system_prompt_dossier()
                user_prompt = prompts.get_user_prompt_dossier(
                    gene_symbol=payload.get("target", "N/A"),
                    disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
                    payload_json=json.dumps(payload, indent=1),
                )
            elif fmt == "compiler":
                system_prompt = prompts.get_system_prompt_compiler()
                user_prompt = prompts.get_user_prompt_compiler(
                    gene_symbol=payload.get("target", "N/A"),
                    disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
                    payload_json=json.dumps(payload, indent=1),
                )
            else:
                system_prompt = prompts.get_system_prompt()
                user_prompt = prompts.get_user_prompt(
                    gene_symbol=payload.get("target", "N/A"),
                    disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
                    payload_json=json.dumps(payload, indent=1),
                )
            persist_prompt_trace(
                run_id=request.run_id,
                agent_name="summary_agent",
                stage_name="generate_explanation",
                model=self.model,
                provider=None,
                system_prompt=inject_content_memory(system_prompt),
                user_prompt=user_prompt,
                extra={"report_format": fmt, "evidence_count": len(item_list)},
            )
            response = await ainvoke_with_fallbacks(
                prompt=[
                    ("system", inject_content_memory(system_prompt)),
                    ("user", user_prompt),
                ],
                primary_model=self.model,
                role="reasoning" if fmt in {"dossier", "compiler"} else "fast",
                temperature=self.temperature,
            )
            
            # 1. Robust extraction from potentially multi-part or structured AIMessage content.
            # Some providers (like Gemini) return content as a list of dicts/text parts.
            content_val = response.content
            if isinstance(content_val, str):
                raw_text = content_val
            elif isinstance(content_val, list):
                parts = []
                for p in content_val:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict) and "text" in p:
                        parts.append(p["text"])
                    else:
                        # Fallback for unexpected part types (e.g. tool_calls or artifacts)
                        parts.append(str(p))
                raw_text = "".join(parts)
            else:
                raw_text = str(content_val)

            # 2. Strip potential LLM markdown artifacts (triple backticks)
            # Some models wrap the entire report in a code block despite "Return ONLY report" instructions.
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                import re
                # Strip ```markdown and ending ```
                raw_text = re.sub(r"^```[a-zA-Z]*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text)
                raw_text = raw_text.strip()
            
            # 3. Fix literal newline escaping if the LLM outputted real character sequences "\" + "n"
            # This happens if there's an encoding/transport mismatch in the specific model version.
            if "\\n" in raw_text and "\n" not in raw_text:
                raw_text = raw_text.replace("\\n", "\n")

            combined_text = raw_text.strip()
            if os.getenv("A4T_SUMMARY_APPENDIX_TABLES", "1").strip().lower() not in {"0", "false", "no"}:
                appendix = self._build_compiler_tables_appendix(
                    request=request,
                    items=item_list,
                    source_status=status_list,
                    evidence_dashboard_path=evidence_dashboard_path,
                )
                combined_text = combined_text + "\n\n---\n\n" + appendix

            combined_text = self._inject_header_and_dashboard(
                combined_text,
                gene_symbol=request.gene_symbol,
                run_id=request.run_id,
                evidence_dashboard_path=evidence_dashboard_path,
            )

            summary = LLMSummary(markdown_report=combined_text, generation_mode="llm_raw_text")
            
            summary.robustness = self._build_robustness(status_list, item_count=len(item_list))
            summary.model_used = self.model
            valid, _reason = validate_summary_markdown(summary.markdown_report, item_list)
            if not valid:
                if require_llm_agents():
                    raise RuntimeError(f"summary_agent produced invalid report (no fallback): {_reason}")
                return self._deterministic_fallback(
                    request,
                    item_list,
                    status_list,
                    verification_report=verification_report,
                    conflicts=conflicts,
                    evidence_graph=evidence_graph,
                )
            return summary
        except Exception as exc:
            if require_llm_agents():
                raise RuntimeError(f"summary_agent failed: {exc}") from exc
            return self._deterministic_fallback(
                request,
                item_list,
                status_list,
                verification_report=verification_report,
                conflicts=conflicts,
                evidence_graph=evidence_graph,
                evidence_dashboard_path=evidence_dashboard_path,
            )

    def _system_prompt(self) -> str:
        if self._report_format() == "dossier":
            return prompts.get_system_prompt_dossier()
        if self._report_format() == "compiler":
            return prompts.get_system_prompt_compiler()
        return prompts.get_system_prompt()

    def _user_prompt(self, payload: dict) -> str:
        if self._report_format() == "dossier":
            return prompts.get_user_prompt_dossier(
                gene_symbol=payload.get("target", "N/A"),
                disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
                payload_json=json.dumps(payload, indent=1),
            )
        if self._report_format() == "compiler":
            return prompts.get_user_prompt_compiler(
                gene_symbol=payload.get("target", "N/A"),
                disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
                payload_json=json.dumps(payload, indent=1),
            )
        return prompts.get_user_prompt(
            gene_symbol=payload.get("target", "N/A"),
            disease_context=payload.get("disease_context", "an undefined indication") or "an undefined indication",
            payload_json=json.dumps(payload, indent=1),
        )
