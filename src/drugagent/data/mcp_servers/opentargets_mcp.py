from __future__ import annotations

from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from drugagent.pipeline.collector_service import collect_evidence_bundle
from drugagent.core.schema import SourceName
from .common import SourceCollectInput, build_request, format_source_result


opentargets_mcp = FastMCP("opentargets_mcp")


@opentargets_mcp.tool(
    name="opentargets_collect_target_evidence",
    description=(
        "Collect target–disease association evidence from Open Targets via the public GraphQL API. "
        "This is a read-only evidence retrieval tool."
    ),
    annotations=cast(
        Any,
        {
            "title": "Open Targets Collect Target Evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    ),
)
async def opentargets_collect_target_evidence(params: SourceCollectInput) -> str | dict:
    request = build_request(params, SourceName.OPENTARGETS)
    result = await collect_evidence_bundle(request)
    return format_source_result(result, SourceName.OPENTARGETS, params.response_format)


if __name__ == "__main__":
    opentargets_mcp.run(transport="stdio")

