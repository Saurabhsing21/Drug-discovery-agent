from __future__ import annotations

from collections import defaultdict

from drugagent.core.schema import ConflictRecord, ConflictSeverity, EvidenceRecord, SourceName
from drugagent.data.bio_context import fetch_reactome_pathways


def analyze_conflicts(items: list[EvidenceRecord]) -> list[ConflictRecord]:
    grouped: dict[tuple[str, str | None, str], list[EvidenceRecord]] = defaultdict(list)
    conflicts: list[ConflictRecord] = []

    for item in items:
        grouped[(item.target_symbol, item.disease_id, item.evidence_type)].append(item)

    for (target_symbol, disease_id, evidence_type), group in grouped.items():
        sources = {item.source for item in group}
        scores = [item.normalized_score for item in group if item.normalized_score is not None]
        if len(sources) < 2 or len(scores) < 2:
            continue

        spread = max(scores) - min(scores)
        severity = None
        if min(scores) <= 0.25 and max(scores) >= 0.75:
            severity = ConflictSeverity.HIGH
        elif spread >= 0.35:
            severity = ConflictSeverity.MEDIUM
        elif spread >= 0.2:
            severity = ConflictSeverity.LOW

        if severity is None:
            continue

        # Enrich the rationale with Reactome pathway context.
        # This turns "score spread=0.4" into a biologically interpretable
        # statement: which pathways is this gene part of, and is the
        # conflict expected given the disease context?
        pathways = fetch_reactome_pathways(target_symbol)
        pathway_clause = ""
        if pathways:
            pathway_str = "; ".join(pathways[:3])
            pathway_clause = (
                f" Reactome pathway context: {target_symbol} participates in "
                f"{pathway_str}. "
                f"Evaluate whether this conflict is disease-subtype specific "
                f"(e.g. target is essential only in a particular tissue lineage) "
                f"or reflects a true evidence gap."
            )

        conflicts.append(
            ConflictRecord(
                severity=severity,
                rationale=(
                    f"Conflicting {evidence_type} evidence for {target_symbol}"
                    f"{' in ' + disease_id if disease_id else ''}: "
                    f"score spread={spread:.3f}."
                    f"{pathway_clause}"
                ),
                sources=[
                    SourceName(source)
                    for source in sorted(
                        (source.value if hasattr(source, "value") else str(source) for source in sources)
                    )
                ],
                evidence_ids=[
                    item.evidence_id
                    for item in group
                ],
            )
        )

    return conflicts
