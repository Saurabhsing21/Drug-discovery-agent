from __future__ import annotations

import json
import os
from pathlib import Path

from drugagent.core.schema import (
    ConflictRecord,
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceGraphSnapshot,
    EvidenceRecord,
    GraphEdgeType,
)


def _artifact_root(root: str | Path | None = None) -> Path:
    return Path(
        root
        or os.getenv("A4T_ARTIFACT_DIR")
        or Path(__file__).resolve().parent.parent / "artifacts"
    )


def _publication_identifiers(item: EvidenceRecord) -> list[tuple[str, str]]:
    support = item.support or {}
    identifiers: list[tuple[str, str]] = []
    for key in ("pmid", "pmcid", "doi"):
        value = support.get(key)
        if value:
            identifiers.append((key, str(value).strip()))
    return identifiers


def build_evidence_graph_snapshot(
    items: list[EvidenceRecord],
    conflicts: list[ConflictRecord] | None = None,
) -> EvidenceGraphSnapshot:
    nodes: list[EvidenceGraphNode] = []
    edges: list[EvidenceGraphEdge] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    conflict_index = {
        evidence_id: conflict.severity
        for conflict in (conflicts or [])
        for evidence_id in conflict.evidence_ids
    }

    def add_node(node_id: str, node_type: str, label: str, **attributes) -> None:
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        nodes.append(
            EvidenceGraphNode(
                id=node_id,
                node_type=node_type,
                label=label,
                attributes=attributes,
            )
        )

    def add_edge(source_id: str, target_id: str, edge_type: GraphEdgeType, **attributes) -> None:
        edge_key = (source_id, target_id, edge_type.value)
        if edge_key in seen_edges:
            return
        seen_edges.add(edge_key)
        edges.append(
            EvidenceGraphEdge(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                attributes=attributes,
            )
        )

    for idx, item in enumerate(items, start=1):
        source_name = item.source.value if hasattr(item.source, "value") else str(item.source)
        evidence_id = item.evidence_id
        target_node = f"target:{item.target_symbol}"
        evidence_node = f"evidence:{evidence_id}"
        source_node = f"source:{source_name}"

        add_node(target_node, "target", item.target_symbol, target_id=item.target_id)
        add_node(
            evidence_node,
            "evidence",
            item.evidence_type,
            source=source_name,
            evidence_id=evidence_id,
            normalized_score=item.normalized_score,
            confidence=item.confidence,
            conflict_severity=conflict_index.get(evidence_id),
        )
        add_node(source_node, "source", source_name, provider=item.provenance.provider)

        add_edge(target_node, evidence_node, GraphEdgeType.TARGET_EVIDENCE)
        add_edge(evidence_node, source_node, GraphEdgeType.EVIDENCE_SOURCE)

        if item.disease_id:
            disease_node = f"disease:{item.disease_id}"
            add_node(disease_node, "disease", item.disease_id)
            add_edge(target_node, disease_node, GraphEdgeType.TARGET_DISEASE)

        for id_type, identifier in _publication_identifiers(item):
            publication_node = f"publication:{id_type}:{identifier}"
            add_node(publication_node, "publication", identifier, identifier_type=id_type)
            add_edge(
                evidence_node,
                publication_node,
                GraphEdgeType.EVIDENCE_PUBLICATION,
            )

    nodes.sort(key=lambda node: node.id)
    edges.sort(
        key=lambda edge: (
            edge.source_id,
            edge.target_id,
            edge.edge_type if isinstance(edge.edge_type, str) else edge.edge_type.value,
        )
    )
    return EvidenceGraphSnapshot(nodes=nodes, edges=edges)


def persist_evidence_graph_snapshot(
    run_id: str,
    snapshot: EvidenceGraphSnapshot,
    root: str | Path | None = None,
) -> EvidenceGraphSnapshot:
    artifact_dir = _artifact_root(root) / "graphs"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{run_id}.evidence_graph.json"
    snapshot_with_path = snapshot.model_copy(update={"artifact_path": str(artifact_path)})
    artifact_path.write_text(
        json.dumps(snapshot_with_path.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return snapshot_with_path
