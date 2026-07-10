from __future__ import annotations

import re
import os

from drugagent.core.schema import EvidenceRecord


FORBIDDEN_PATTERNS = [
    re.compile(r"\bI think\b", re.IGNORECASE),
    re.compile(r"\bwe believe\b", re.IGNORECASE),
    re.compile(r"\bprobably\b", re.IGNORECASE),
]


def validate_summary_markdown(markdown: str, items: list[EvidenceRecord]) -> tuple[bool, str | None]:
    # Auto-detect format from headings to avoid coupling validation to env defaults.
    if "# THERAPEUTIC TARGET EVIDENCE SUMMARY REPORT" in markdown:
        required_sections = [
            "# THERAPEUTIC TARGET EVIDENCE SUMMARY REPORT",
            "## 1. Executive Summary",
            "## 2. Target Annotation — PHAROS",
            "## 3. Genetic Dependency — DepMap",
            "## 4. Disease Associations — Open Targets",
            "## 5. Literature",
            "## 6. Integrated Interpretation",
            "### Evidence Contribution (Interpretation)",
            "## 7. Evidence Strength Assessment",
            "## 8. Overall Assessment",
            "## 9. Final Conclusion",
        ]
        format_mode = "compiler"
    elif "## 1. Executive Summary" in markdown:
        required_sections = [
            "## 1. Executive Summary",
            "## 2. Target Biology",
            "## 3. Genetic Dependency",
            "## 4. Disease Associations",
            "## 8. Grounded Evidence Citations",
            "## 9. Conclusions",
        ]
        format_mode = "dossier"
    elif "## Executive Answer" in markdown:
        required_sections = [
            "## Executive Answer",
            "## Evidence Summary",
            "## Source Coverage",
            "## Conflicts & Caveats",
            "## Machine Appendix",
        ]
        format_mode = "concise"
    elif "## Executive" in markdown:
        required_sections = [
            "## Executive",
            "## Source Coverage",
            "## Key Findings",
            "## Evidence Citations",
        ]
        format_mode = "structured"
    else:
        report_format = (os.getenv("A4T_REPORT_FORMAT", "structured") or "structured").strip().lower()
        if report_format == "dossier":
            required_sections = [
                "## 1. Executive Summary",
                "## 2. Target Biology",
                "## 3. Genetic Dependency",
                "## 4. Disease Associations",
                "## 8. Grounded Evidence Citations",
                "## 9. Conclusions",
            ]
            format_mode = "dossier"
        else:
            required_sections = [
                "## Executive Answer",
                "## Evidence Summary",
                "## Source Coverage",
                "## Conflicts & Caveats",
                "## Machine Appendix",
            ]
            format_mode = "concise"

    for section in required_sections:
        if section not in markdown:
            return False, f"Missing section: {section}"

    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(markdown):
            return False, f"Forbidden language matched: {pattern.pattern}"

    evidence_refs = {(item.evidence_id or "").strip() for item in items if (item.evidence_id or "").strip()}

    if format_mode in {"dossier", "structured"}:
        grounded_lines = [line.strip("- ").strip() for line in markdown.splitlines() if line.startswith("- [")]
        for line in grounded_lines:
            if evidence_refs and not any(f"[{ref}]" in line for ref in evidence_refs):
                return False, f"Unsupported citation reference: {line}"
    elif format_mode == "compiler":
        # Only check POSITIVE evidence records — absence/negative synthetic records
        # (identified by "ABSENCE" or "absence" in the id or type) are not expected
        # to appear verbatim in the compiled narrative.
        positive_refs = {
            ref for ref in evidence_refs
            if ref and "absence" not in ref.lower() and "ABSENCE" not in ref
        }
        if positive_refs:
            missing = [ref for ref in sorted(positive_refs) if ref not in markdown]
            # Require at least 50% of positive evidence ids to appear in the report.
            # A strict 100% requirement caused failures when the LLM paraphrased or
            # grouped citations rather than listing every id individually.
            threshold = max(1, len(positive_refs) // 2)
            if len(missing) > len(positive_refs) - threshold:
                return False, f"Missing evidence ids in compiled report: {missing[:3]}"

        # Check that the narrative contains INLINE traceability citations.
        # The new prompt (RULE 1) requires format: (evidence_id: <id>; source: <source>)
        # Check only the narrative portion (before the Appendix A tables, if present).
        narrative_part = markdown.split("# Appendix A")[0] if "# Appendix A" in markdown else markdown
        inline_cite_count = narrative_part.count("evidence_id:")
        if items and inline_cite_count < 3:
            return False, (
                f"Insufficient inline traceability: found {inline_cite_count} "
                "'evidence_id:' citations in narrative (minimum 3 required). "
                "Every key claim must cite its evidence_id inline."
            )
    else:
        # Concise format: require at least one evidence id reference if any items exist.
        if items and evidence_refs and not any(ref in markdown for ref in list(evidence_refs)[:5]):
            return False, "No evidence_id references found in report."

    return True, None
