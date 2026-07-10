from __future__ import annotations

import json
import re

from drugagent.memory.semantic import disease_aliases, gene_aliases
from drugagent.core.schema import CollectorRequest, EvidenceRecord, SourceName, SourceStatus, StatusName, VerificationReport, VerificationRuleOutcome


ONTOLOGY_ID_RE = re.compile(r"^(EFO|MONDO)[:_]\d+$", re.IGNORECASE)


def _evidence_ref(item: EvidenceRecord, idx: int) -> str:
    return item.evidence_id or f"{item.source}:{item.target_id}:{item.disease_id or 'NA'}:{item.evidence_type}:{idx}"


def _rule(
    name: str,
    passed: bool,
    *,
    blocking: bool = False,
    evidence_ids: list[str] | None = None,
    message: str | None = None,
) -> VerificationRuleOutcome:
    return VerificationRuleOutcome(
        rule_name=name,
        passed=passed,
        blocking=blocking,
        evidence_ids=evidence_ids or [],
        message=message,
    )


def run_verification(
    request: CollectorRequest,
    items: list[EvidenceRecord],
    *,
    source_status: list[SourceStatus] | None = None,
) -> VerificationReport:
    outcomes: list[VerificationRuleOutcome] = []
    refs = [_evidence_ref(item, idx) for idx, item in enumerate(items, start=1)]

    invalid_schema_refs: list[str] = []
    missing_provenance_refs: list[str] = []
    missing_evidence_id_refs: list[str] = []
    duplicate_refs: list[str] = []
    gene_mismatch_refs: list[str] = []
    disease_mismatch_refs: list[str] = []
    missing_citation_refs: list[str] = []
    bad_ontology_refs: list[str] = []

    if not items:
        outcomes.append(
            _rule(
                "minimum_evidence_presence",
                False,
                blocking=True,
                evidence_ids=[],
                message="No evidence records were collected for this run.",
            )
        )

    if source_status and not any(status.status == StatusName.SUCCESS for status in source_status):
        outcomes.append(
            _rule(
                "minimum_source_success",
                False,
                blocking=True,
                evidence_ids=[],
                message="No requested source completed successfully.",
            )
        )

    seen_fingerprints: dict[str, str] = {}

    for idx, item in enumerate(items, start=1):
        ref = refs[idx - 1]

        try:
            EvidenceRecord.model_validate(item.model_dump(mode="json"))
        except Exception:
            invalid_schema_refs.append(ref)

        if not item.provenance.provider.strip() or not item.provenance.endpoint.strip():
            missing_provenance_refs.append(ref)

        if not (item.evidence_id or "").strip():
            missing_evidence_id_refs.append(ref)

        fingerprint = json.dumps(
            {
                "evidence_id": item.evidence_id,
            },
            sort_keys=True,
            default=str,
        )
        if fingerprint in seen_fingerprints:
            duplicate_refs.extend([seen_fingerprints[fingerprint], ref])
        else:
            seen_fingerprints[fingerprint] = ref

        if item.target_symbol.strip().upper() not in gene_aliases(request.gene_symbol):
            gene_mismatch_refs.append(ref)

        if request.disease_id and item.disease_id and item.disease_id not in disease_aliases(request.disease_id):
            disease_mismatch_refs.append(ref)

        if item.source == SourceName.LITERATURE:
            pmid = item.support.get("pmid")
            pmcid = item.support.get("pmcid")
            if not pmid and not pmcid:
                missing_citation_refs.append(ref)

        if item.disease_id and not ONTOLOGY_ID_RE.match(item.disease_id):
            bad_ontology_refs.append(ref)

    outcomes.append(
        _rule(
            "schema_validity",
            not invalid_schema_refs,
            blocking=True,
            evidence_ids=sorted(set(invalid_schema_refs)),
            message="All evidence records validate against the canonical schema." if not invalid_schema_refs else "One or more evidence records failed schema validation.",
        )
    )
    outcomes.append(
        _rule(
            "provenance_completeness",
            not missing_provenance_refs,
            blocking=True,
            evidence_ids=sorted(set(missing_provenance_refs)),
            message="All evidence records include required provenance fields." if not missing_provenance_refs else "Missing provenance provider and/or endpoint fields detected.",
        )
    )
    outcomes.append(
        _rule(
            "evidence_id_presence",
            not missing_evidence_id_refs,
            blocking=True,
            evidence_ids=sorted(set(missing_evidence_id_refs)),
            message="All evidence records include a canonical evidence_id." if not missing_evidence_id_refs else "One or more evidence records are missing evidence_id.",
        )
    )
    outcomes.append(
        _rule(
            "duplicate_detection",
            not duplicate_refs,
            blocking=False,
            # Keep duplicates (same evidence_id can appear multiple times) to reflect occurrence count.
            evidence_ids=sorted(duplicate_refs),
            message="No duplicate evidence fingerprints detected." if not duplicate_refs else "Duplicate evidence records detected.",
        )
    )
    outcomes.append(
        _rule(
            "gene_mapping_consistency",
            not gene_mismatch_refs,
            blocking=True,
            evidence_ids=sorted(set(gene_mismatch_refs)),
            message="All evidence records match the requested gene symbol." if not gene_mismatch_refs else "Evidence contains target symbol mismatches.",
        )
    )
    outcomes.append(
        _rule(
            "disease_mapping_consistency",
            not disease_mismatch_refs,
            blocking=False,
            evidence_ids=sorted(set(disease_mismatch_refs)),
            message="All evidence records match the requested disease context." if not disease_mismatch_refs else "Evidence contains disease mapping mismatches.",
        )
    )
    outcomes.append(
        _rule(
            "citation_presence",
            not missing_citation_refs,
            blocking=False,
            evidence_ids=sorted(set(missing_citation_refs)),
            message="All literature evidence includes a PMID or PMCID." if not missing_citation_refs else "Literature evidence is missing citation identifiers.",
        )
    )
    outcomes.append(
        _rule(
            "ontology_id_format",
            not bad_ontology_refs,
            blocking=False,
            evidence_ids=sorted(set(bad_ontology_refs)),
            message="All disease identifiers match EFO/MONDO format." if not bad_ontology_refs else "Invalid disease identifier format detected.",
        )
    )

    fail_count = sum(1 for outcome in outcomes if not outcome.passed)
    blocking_issues = [outcome.rule_name for outcome in outcomes if not outcome.passed and outcome.blocking]
    warning_issues = [outcome.rule_name for outcome in outcomes if not outcome.passed and not outcome.blocking]
    affected_evidence_ids = sorted(
        {
            evidence_id
            for outcome in outcomes
            for evidence_id in outcome.evidence_ids
        }
    )

    return VerificationReport(
        total_rules=len(outcomes),
        pass_count=sum(1 for outcome in outcomes if outcome.passed),
        fail_count=fail_count,
        warning_count=len(warning_issues),
        blocked=bool(blocking_issues),
        blocking_issue_count=len(blocking_issues),
        blocking_issues=blocking_issues,
        warning_issues=warning_issues,
        affected_evidence_ids=affected_evidence_ids,
        rule_outcomes=outcomes,
    )
