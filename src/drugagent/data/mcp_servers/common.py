from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drugagent.core.schema import CollectorRequest, CollectorResult, ErrorRecord, SourceName, SourceStatus


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class SourceCollectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gene_symbol: str = Field(..., min_length=1, description="Target HGNC symbol (example: EGFR)")
    disease_id: str | None = Field(default=None, description="Disease identifier (example: EFO_0000311)")
    species: str = Field(default="Homo sapiens", description="Species label for the query")
    per_source_top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Target number of evidence records to return for this source when available.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format. Use json for machine processing or markdown for readability.",
    )
    run_id: str | None = Field(default=None, description="Optional id for traceability")


class LiteratureCollectInput(SourceCollectInput):
    max_literature_articles: int = Field(default=5, ge=1, le=20)


def build_request(params: SourceCollectInput | LiteratureCollectInput, source: SourceName) -> CollectorRequest:
    payload: dict[str, Any] = {
        "gene_symbol": params.gene_symbol,
        "disease_id": params.disease_id,
        "species": params.species,
        "per_source_top_k": params.per_source_top_k,
        "sources": [source],
    }

    if params.run_id:
        payload["run_id"] = params.run_id

    if isinstance(params, LiteratureCollectInput):
        payload["max_literature_articles"] = params.max_literature_articles

    return CollectorRequest.model_validate(payload)


def _status_markdown(status: SourceStatus) -> str:
    return (
        f"- status: **{status.status}**\n"
        f"- duration_ms: {status.duration_ms}\n"
        f"- record_count: {status.record_count}\n"
        f"- error_code: {status.error_code or 'none'}\n"
        f"- error_message: {status.error_message or 'none'}"
    )


def _errors_markdown(errors: list[ErrorRecord]) -> str:
    if not errors:
        return "none"

    lines = []
    for err in errors:
        lines.append(f"- `{err.error_code}`: {err.message} (retryable={err.retryable})")
    return "\n".join(lines)


def format_source_result(
    result: CollectorResult,
    source: SourceName,
    response_format: ResponseFormat,
) -> str | dict[str, Any]:
    source_items = [item for item in result.items if item.source == source]
    source_status = [entry for entry in result.source_status if entry.source == source]
    source_errors = [entry for entry in result.errors if entry.source == source]

    payload = {
        "schema_version": result.schema_version,
        "run_id": result.run_id,
        "source": source.value,
        "query": result.query.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in source_items],
        "source_status": [entry.model_dump(mode="json") for entry in source_status],
        "errors": [entry.model_dump(mode="json") for entry in source_errors],
    }

    if response_format == ResponseFormat.JSON:
        return payload

    title = f"# {source.value.upper()} evidence for {result.query.gene_symbol}"
    disease_line = f"- disease_id: {result.query.disease_id or 'none'}"
    status_text = _status_markdown(source_status[0]) if source_status else "- status: **missing**"
    errors_text = _errors_markdown(source_errors)

    item_lines: list[str] = []
    if not source_items:
        item_lines.append("No evidence items returned.")
    else:
        for idx, item in enumerate(source_items, start=1):
            item_lines.append(
                f"{idx}. `{item.evidence_type}` score={item.normalized_score} confidence={item.confidence} "
                f"summary={item.summary or 'n/a'}"
            )

    return "\n\n".join(
        [
            title,
            disease_line,
            "- final_score: n/a\n- aggregate_confidence: n/a",
            "## Source status\n" + status_text,
            "## Evidence items\n" + "\n".join(item_lines),
            "## Errors\n" + errors_text,
        ]
    )
