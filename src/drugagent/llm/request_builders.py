from __future__ import annotations

from typing import Iterable

from drugagent.core.schema import CollectorRequest, SourceName


DEFAULT_SOURCES = [
    SourceName.DEPMAP,
    SourceName.PHAROS,
    SourceName.OPENTARGETS,
    SourceName.LITERATURE,
]


def build_collector_request(
    *,
    gene_symbol: str,
    disease_id: str | None = None,
    objective: str | None = None,
    species: str = "Homo sapiens",
    sources: Iterable[SourceName] | None = None,
    per_source_top_k: int = 5,
    max_literature_articles: int = 5,
    model_override: str | None = None,
    run_id: str | None = None,
) -> CollectorRequest:
    payload = {
        "gene_symbol": gene_symbol.strip().upper(),
        "disease_id": disease_id,
        "objective": objective,
        "species": species,
        "sources": list(sources) if sources is not None else list(DEFAULT_SOURCES),
        "per_source_top_k": per_source_top_k,
        "max_literature_articles": max_literature_articles,
        "model_override": model_override,
    }
    if run_id:
        payload["run_id"] = run_id
    return CollectorRequest.model_validate(payload)
