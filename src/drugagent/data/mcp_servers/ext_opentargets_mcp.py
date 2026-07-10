"""
External Open Targets MCP Server
---------------------------------
Thin FastMCP server that proxies the official Open Targets Platform MCP
(`otp-mcp --transport stdio`) and translates its raw GraphQL response into
our standard EvidenceRecord schema.

Tool exposed: `ext_opentargets_collect_target_evidence`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
import shutil

from mcp.server.fastmcp import FastMCP

from .common import SourceCollectInput, build_request, format_source_result
from drugagent.core.schema import (
    CollectorRequest,
    EvidenceRecord,
    ErrorCode,
    Provenance,
    SourceName,
    SourceStatus,
    StatusName,
    CollectorResult,
)

mcp = FastMCP("ext-opentargets-mcp")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOCAL_OTP_HOME = os.path.abspath(os.path.join(_PROJECT_ROOT, "external_mcps", "open-targets-platform-mcp"))
_LOCAL_OTP_BIN = os.path.abspath(os.path.join(_LOCAL_OTP_HOME, ".venv", "bin", "otp-mcp"))

def _otp_bin() -> str:
    override = os.getenv("A4T_OTP_MCP_BIN", "").strip()
    if override:
        return override
    found = shutil.which("otp-mcp")
    if found:
        return found
    return _LOCAL_OTP_BIN

def _otp_home() -> str:
    override = os.getenv("A4T_OTP_MCP_HOME", "").strip()
    if override:
        return override
    return _LOCAL_OTP_HOME

_BASE_URL = "https://api.platform.opentargets.org/api/v4/graphql"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: spawn otp-mcp and call one tool
# ─────────────────────────────────────────────────────────────────────────────

async def _call_otp_tool(tool_name: str, arguments: dict) -> dict | list | str | None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=_otp_bin(),
        args=["--transport", "stdio"],
        env={**os.environ, "HOME": _otp_home(), "FASTMCP_LOG_LEVEL": "ERROR"},
        cwd=_otp_home() if os.path.isdir(_otp_home()) else os.getcwd(),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            result = await s.call_tool(tool_name, arguments)

    text_parts = [
        getattr(c, "text", "")
        for c in getattr(result, "content", [])
        if getattr(c, "type", None) == "text"
    ]
    text = "\n".join(p for p in text_parts if p)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: search_entities → get Ensembl ID  (takes `query_strings: list[str]`)
# Step 2: query_open_targets_graphql → rich association data
# ─────────────────────────────────────────────────────────────────────────────

_ASSOCIATION_QUERY = """
query TargetAssociations($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    approvedName
    biotype
    associatedDiseases(page: {index: 0, size: 50}) {
      count
      rows {
        score
        disease { id name }
      }
    }
    tractability { label modality value }
  }
}
"""


async def _fetch_evidence(request: CollectorRequest) -> tuple[list[EvidenceRecord], SourceStatus, list]:
    started_at = time.perf_counter()

    def _ms():
        return int((time.perf_counter() - started_at) * 1000)

    def _skip(msg):
        return [], SourceStatus(source=SourceName.OPENTARGETS, status=StatusName.SKIPPED, duration_ms=_ms(), record_count=0, error_message=msg), []

    def _fail(msg):
        return [], SourceStatus(source=SourceName.OPENTARGETS, status=StatusName.FAILED, duration_ms=_ms(), record_count=0, error_code=ErrorCode.UPSTREAM_ERROR, error_message=msg), []

    try:
        # ── Step 1: resolve gene symbol → Ensembl ID ──────────────────────────
        search_result = await _call_otp_tool("search_entities", {
            "query_strings": [request.gene_symbol],
        })

        ensembl_id = None
        if isinstance(search_result, dict):
            # Actual otp-mcp shape:
            # {"results": [{"key": "EGFR", "result": {"result": [[{"id": "ENSG...", "entity": "target"}, ...]]}}]}
            for entry in search_result.get("results", []):
                inner = entry.get("result", {})
                hit_lists = inner.get("result", [])  # list of lists (one per query)
                for hit_list in hit_lists:
                    if isinstance(hit_list, list):
                        for hit in hit_list:
                            if str(hit.get("entity", "")).lower() == "target":
                                ensembl_id = hit.get("id")
                                break
                    if ensembl_id:
                        break
                if ensembl_id:
                    break

        if not ensembl_id:
            return _skip(f"Could not resolve '{request.gene_symbol}' to Ensembl ID via ext OT MCP")

        # ── Step 2: fetch association data ────────────────────────────────────
        gql_result = await _call_otp_tool("query_open_targets_graphql", {
            "query_string": _ASSOCIATION_QUERY,
            "variables": {"ensemblId": ensembl_id},
        })

        target_data: dict[str, Any] = {}
        if isinstance(gql_result, dict):
            # Try multiple nesting patterns (some MCPs wrap in 'result', some don't)
            target_data = (
                gql_result.get("data", {}).get("target")
                or gql_result.get("result", {}).get("data", {}).get("target")
                or gql_result.get("result", {}).get("target")
                or gql_result.get("target")
                or {}
            )

        if not target_data:
            return _skip(f"No target data returned for ensembl_id={ensembl_id}")

        rows = target_data.get("associatedDiseases", {}).get("rows", [])
        if not rows:
            return _skip(f"No disease associations found for '{request.gene_symbol}'")

        assoc_count = int(target_data.get("associatedDiseases", {}).get("count") or 0)

        tractability = target_data.get("tractability") or []
        trac_summary = {t["modality"]: t["label"] for t in tractability if t.get("value")}

        ranked_rows = sorted(rows, key=lambda r: float(r.get("score") or 0.0), reverse=True)
        top_k = max(1, int(request.per_source_top_k))
        chosen_rows = ranked_rows[:top_k]

        if request.disease_id:
            match = next(
                (r for r in ranked_rows if (r.get("disease", {}).get("id") or "").lower() == request.disease_id.lower()),
                None,
            )
            if match and match not in chosen_rows:
                chosen_rows = [match] + chosen_rows[: max(0, top_k - 1)]

        records: list[EvidenceRecord] = []
        for idx, row in enumerate(chosen_rows, start=1):
            score = float(row.get("score") or 0.0)
            disease = row.get("disease") or {}
            disease_id = disease.get("id")
            disease_name = disease.get("name")
            confidence = min(0.95, max(0.2, 0.55 + (score * 0.3) + min(assoc_count / 2000, 0.1)))
            records.append(
                EvidenceRecord(
                    source=SourceName.OPENTARGETS,
                    target_id=f"{target_data.get('id') or ensembl_id}:{disease_id or idx}",
                    target_symbol=target_data.get("approvedSymbol") or request.gene_symbol,
                    disease_id=disease_id,
                    evidence_type="disease_association",
                    raw_value=score,
                    normalized_score=max(0.0, min(1.0, score)),
                    confidence=confidence,
                    support={
                        "rank": idx,
                        "association_count": assoc_count,
                        "disease_name": disease_name,
                        "approved_name": target_data.get("approvedName"),
                        "biotype": target_data.get("biotype"),
                        "tractability": trac_summary,
                        "source": "Official Open Targets Platform MCP",
                    },
                    summary=(
                        f"[Official OT MCP] {request.gene_symbol} ↔ {disease_name or disease_id}: "
                        f"score={score:.3f}, rank={idx}/{len(chosen_rows)} ({assoc_count} associations total)."
                    ),
                    provenance=Provenance(
                        provider="Open Targets (official MCP)",
                        endpoint=_BASE_URL,
                        query={"gene_symbol": request.gene_symbol, "ensembl_id": ensembl_id, "disease_id": disease_id},
                    ),
                )
            )

        status = SourceStatus(
            source=SourceName.OPENTARGETS,
            status=StatusName.SUCCESS,
            duration_ms=_ms(),
            record_count=len(records),
        )
        return records, status, []

    except Exception as exc:  # noqa: BLE001
        return _fail(f"ext_opentargets error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="ext_opentargets_collect_target_evidence",
    description="Collect disease association evidence from the official Open Targets Platform MCP server.",
)
async def ext_opentargets_collect_target_evidence(params: SourceCollectInput) -> str | dict:
    """Retrieve evidence via the official Open Targets Platform MCP."""
    request = build_request(params, SourceName.OPENTARGETS)
    items, source_status, errors = await _fetch_evidence(request)
    result = CollectorResult(
        run_id=request.run_id,
        query=request,
        items=items,
        source_status=[source_status],
        errors=errors,
    )
    return format_source_result(result, SourceName.OPENTARGETS, params.response_format)


if __name__ == "__main__":
    mcp.run(transport="stdio")
