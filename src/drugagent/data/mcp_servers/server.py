from __future__ import annotations

from enum import Enum
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from drugagent.pipeline.collector_service import collect_evidence_bundle
from drugagent.llm.request_builders import build_collector_request
from drugagent.core.schema import CollectorRequest, SourceName


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class BundleCollectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gene_symbol: str = Field(..., min_length=1)
    disease_id: str | None = None
    objective: str | None = None
    species: str = "Homo sapiens"
    sources: list[SourceName] | None = None
    per_source_top_k: int = Field(default=5, ge=1, le=20)
    max_literature_articles: int = Field(default=5, ge=1, le=20)
    model_override: str | None = None
    run_id: str | None = None
    response_format: ResponseFormat = ResponseFormat.JSON


combined_mcp = FastMCP("agent4target_mcp")


def _tool_annotations(title: str) -> Any:
    # FastMCP's runtime accepts dicts, but the library type expects a ToolAnnotations object.
    # Cast to keep mypy happy while preserving metadata used by MCP UIs.
    return cast(
        Any,
        {
            "title": title,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )


def _build_request(params: BundleCollectInput, force_source: SourceName | None = None) -> CollectorRequest:
    selected_sources = params.sources or [
        SourceName.DEPMAP,
        SourceName.PHAROS,
        SourceName.OPENTARGETS,
        SourceName.LITERATURE,
    ]
    if force_source is not None:
        selected_sources = [force_source]

    return build_collector_request(
        gene_symbol=params.gene_symbol,
        disease_id=params.disease_id,
        objective=params.objective,
        species=params.species,
        sources=selected_sources,
        per_source_top_k=params.per_source_top_k,
        max_literature_articles=params.max_literature_articles,
        model_override=params.model_override,
        run_id=params.run_id,
    )


def _as_markdown(result_dict: dict[str, Any]) -> str:
    lines = [
        f"# Agent4Target evidence bundle for {result_dict['query']['gene_symbol']}",
        f"- run_id: {result_dict['run_id']}",
        f"- disease_id: {result_dict['query'].get('disease_id') or 'none'}",
        f"- items: {len(result_dict.get('items', []))}",
        "",
        "## Source status",
    ]

    for status in result_dict.get("source_status", []):
        lines.append(
            f"- {status.get('source')}: {status.get('status')} "
            f"(records={status.get('record_count')}, error={status.get('error_code') or 'none'})"
        )

    return "\n".join(lines)


async def _collect(params: BundleCollectInput, force_source: SourceName | None = None) -> str | dict[str, Any]:
    request = _build_request(params, force_source=force_source)
    result = await collect_evidence_bundle(request)
    payload = result.model_dump(mode="json")

    if params.response_format == ResponseFormat.MARKDOWN:
        return _as_markdown(payload)
    return payload


@combined_mcp.tool(
    name="collect_evidence_bundle",
    description="Collect evidence bundle across selected sources.",
    annotations=_tool_annotations("Collect Evidence Bundle"),
)
async def collect_evidence_bundle_tool(params: BundleCollectInput) -> str | dict[str, Any]:
    return await _collect(params)


@combined_mcp.tool(
    name="collect_depmap_evidence",
    description="Collect DepMap evidence only.",
    annotations=_tool_annotations("Collect DepMap Evidence"),
)
async def collect_depmap_evidence_tool(params: BundleCollectInput) -> str | dict[str, Any]:
    return await _collect(params, force_source=SourceName.DEPMAP)


@combined_mcp.tool(
    name="collect_pharos_evidence",
    description="Collect PHAROS evidence only.",
    annotations=_tool_annotations("Collect PHAROS Evidence"),
)
async def collect_pharos_evidence_tool(params: BundleCollectInput) -> str | dict[str, Any]:
    return await _collect(params, force_source=SourceName.PHAROS)


@combined_mcp.tool(
    name="collect_opentargets_evidence",
    description="Collect Open Targets evidence only.",
    annotations=_tool_annotations("Collect Open Targets Evidence"),
)
async def collect_opentargets_evidence_tool(params: BundleCollectInput) -> str | dict[str, Any]:
    return await _collect(params, force_source=SourceName.OPENTARGETS)


@combined_mcp.tool(
    name="collect_literature_evidence",
    description="Collect literature evidence only.",
    annotations=_tool_annotations("Collect Literature Evidence"),
)
async def collect_literature_evidence_tool(params: BundleCollectInput) -> str | dict[str, Any]:
    return await _collect(params, force_source=SourceName.LITERATURE)


if __name__ == "__main__":
    combined_mcp.run(transport="stdio")
