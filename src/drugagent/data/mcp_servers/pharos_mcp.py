from __future__ import annotations

from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from drugagent.pipeline.collector_service import collect_evidence_bundle
from drugagent.core.schema import SourceName
from .common import SourceCollectInput, build_request, format_source_result


pharos_mcp = FastMCP("pharos_mcp")


@pharos_mcp.tool(
    name="pharos_collect_target_evidence",
    description=(
        "Collect target-level annotations from PHAROS via the public GraphQL API. "
        "This is a read-only evidence retrieval tool."
    ),
    annotations=cast(
        Any,
        {
            "title": "PHAROS Collect Target Evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    ),
)
async def pharos_collect_target_evidence(params: SourceCollectInput) -> str | dict:
    request = build_request(params, SourceName.PHAROS)
    result = await collect_evidence_bundle(request)
    return format_source_result(result, SourceName.PHAROS, params.response_format)


if __name__ == "__main__":
    pharos_mcp.run(transport="stdio")

