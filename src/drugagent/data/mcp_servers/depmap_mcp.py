from __future__ import annotations

from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from drugagent.pipeline.collector_service import collect_evidence_bundle
from drugagent.core.schema import SourceName
from .common import SourceCollectInput, build_request, format_source_result


depmap_mcp = FastMCP("depmap_mcp")


@depmap_mcp.tool(
    name="depmap_collect_target_evidence",
    description=(
        "Collect target-level genetic perturbation/dependency evidence from DepMap. "
        "This is a read-only evidence retrieval tool."
    ),
    annotations=cast(
        Any,
        {
            "title": "DepMap Collect Target Evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    ),
)
async def depmap_collect_target_evidence(params: SourceCollectInput) -> str | dict:
    """Retrieve DepMap evidence for a target and optional disease context."""
    request = build_request(params, SourceName.DEPMAP)
    result = await collect_evidence_bundle(request)
    return format_source_result(result, SourceName.DEPMAP, params.response_format)


if __name__ == "__main__":
    depmap_mcp.run(transport="stdio")
