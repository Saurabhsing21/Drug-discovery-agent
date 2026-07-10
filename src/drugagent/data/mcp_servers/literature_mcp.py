from __future__ import annotations

from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from drugagent.pipeline.collector_service import collect_evidence_bundle
from drugagent.core.schema import SourceName
from .common import LiteratureCollectInput, build_request, format_source_result


literature_mcp = FastMCP("literature_mcp")


@literature_mcp.tool(
    name="literature_collect_target_evidence",
    description=(
        "Collect summarized literature evidence for a target using the literature connector. "
        "This is a read-only evidence retrieval tool."
    ),
    annotations=cast(
        Any,
        {
            "title": "Literature Collect Target Evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    ),
)
async def literature_collect_target_evidence(params: LiteratureCollectInput) -> str | dict:
    """Retrieve literature evidence for a target and optional disease context."""
    request = build_request(params, SourceName.LITERATURE)
    result = await collect_evidence_bundle(request)
    return format_source_result(result, SourceName.LITERATURE, params.response_format)


if __name__ == "__main__":
    literature_mcp.run(transport="stdio")
