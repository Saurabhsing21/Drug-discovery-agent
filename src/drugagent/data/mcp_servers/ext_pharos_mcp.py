"""
External Pharos MCP Server
---------------------------
Thin FastMCP server that calls the community Pharos MCP server
(TypeScript/Cloudflare Workers, runs locally on port 8787 via `npx wrangler dev`)
using the SSE transport and translates its response into our EvidenceRecord schema.

This MCP exposes: `ext_pharos_collect_target_evidence`

Start the external server first:
    cd external_mcps/pharos-mcp-server
    HOME=. npx wrangler dev --ip 127.0.0.1 --port 8787 --inspector-port 9230
"""
from __future__ import annotations

import json
import os
import time

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

mcp = FastMCP("ext-pharos-mcp")

_PHAROS_MCP_SSE_URL = os.getenv("EXT_PHAROS_MCP_URL", "http://127.0.0.1:8787/sse")
_PHAROS_GRAPHQL_ENDPOINT = "https://pharos-api.ncats.io/graphql"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: call a tool on the external Pharos MCP SSE server
# ─────────────────────────────────────────────────────────────────────────────

async def _call_pharos_tool(tool_name: str, arguments: dict) -> dict | list | str | None:
    """Connect to the local Pharos wrangler dev server via SSE and call a tool."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(_PHAROS_MCP_SSE_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

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
# Pharos query
# ─────────────────────────────────────────────────────────────────────────────

_TARGET_QUERY = """
query targetDetails($sym: String!) {
  target(q: {sym: $sym}) {
    sym
    name
    tdl
    fam
    novelty
    description
    uniProtFunction: props(name: "UniProt Function") { value }
    ligandCounts { name value }
    diseaseAssociationCount
    diseases(top: 50) {
      name
      mondoID
      associationScore: score
    }
  }
}
"""


async def _fetch_evidence(request: CollectorRequest) -> tuple[list[EvidenceRecord], SourceStatus, list]:
    started_at = time.perf_counter()

    try:
        # Try a direct query without variables to be sure
        query_str = f'{{ target(q: {{ sym: "{request.gene_symbol}" }}) {{ sym name tdl fam novelty ligandCounts {{ name value }} }} }}'
        result = await _call_pharos_tool("pharos_graphql_query", {
            "query": query_str,
        })

        target = None
        if isinstance(result, dict):
            # Check for standard GraphQL data nesting OR MCP result wrapping
            target = (
                result.get("data", {}).get("target")
                or result.get("result", {}).get("data", {}).get("target")
                or result.get("result", {}).get("target")
                or result.get("target")
                or {}
            )
        elif isinstance(result, str):
            try:
                parsed = json.loads(result)
                target = (
                    parsed.get("data", {}).get("target")
                    or parsed.get("result", {}).get("data", {}).get("target")
                    or parsed.get("target")
                    or {}
                )
            except Exception:
                target = {}

        if not target or not target.get("sym"):
            msg = f"[ext_pharos] Target '{request.gene_symbol}' not found via external Pharos MCP"
            ms = int((time.perf_counter() - started_at) * 1000)
            return [], SourceStatus(source=SourceName.PHAROS, status=StatusName.SKIPPED, duration_ms=ms, record_count=0, error_message=msg), []

        tdl = target.get("tdl")
        ligand_counts = target.get("ligandCounts") or []
        ligand_total = sum(int(lc.get("value", 0)) for lc in ligand_counts if isinstance(lc.get("value"), (int, float)))
        fam = target.get("fam")
        novelty = target.get("novelty")
        description = target.get("description")
        disease_count = target.get("diseaseAssociationCount", 0)

        # Associated diseases
        diseases = target.get("diseases") or []
        disease_summary_all = [
            {"name": d.get("name"), "mondo_id": d.get("mondoID"), "score": d.get("associationScore")}
            for d in diseases
        ]
        disease_summary = sorted(
            disease_summary_all,
            key=lambda d: float(d.get("score") or 0.0),
            reverse=True,
        )
        top_k = max(1, int(request.per_source_top_k))
        chosen_diseases = disease_summary[:top_k]

        # Scoring
        tdl_scores = {"Tclin": 0.95, "Tchem": 0.75, "Tbio": 0.55, "Tdark": 0.25}
        base_score = tdl_scores.get(tdl or "", 0.45)
        normalized = min(1.0, base_score + min(0.2, ligand_total / 200))
        confidence = min(0.95, 0.6 + (0.1 if tdl else 0.0) + min(ligand_total / 500, 0.15))

        records: list[EvidenceRecord] = []

        # Record 1: target-level annotation.
        records.append(
            EvidenceRecord(
                source=SourceName.PHAROS,
                target_id=target.get("sym") or request.gene_symbol,
                target_symbol=target.get("sym") or request.gene_symbol,
                disease_id=request.disease_id,
                evidence_type="target_annotation",
                raw_value={"tdl": tdl, "ligand_total": ligand_total, "novelty": novelty},
                normalized_score=min(1.0, max(0.0, normalized)),
                confidence=min(0.95, max(0.0, confidence)),
                support={
                    "target_name": target.get("name"),
                    "family": fam,
                    "tdl": tdl,
                    "novelty": novelty,
                    "ligand_total": ligand_total,
                    "disease_association_count": disease_count,
                    "top_diseases": chosen_diseases[:5],
                    "description": (description or "")[:200] if description else None,
                    "source": "Community Pharos MCP (Cloudflare Workers)",
                },
                summary=(
                    f"[ext] Pharos target annotation: {request.gene_symbol} TDL={tdl}, ligands={ligand_total}, "
                    f"family={fam}, disease_association_count={disease_count}."
                ),
                provenance=Provenance(
                    provider="PHAROS (community MCP)",
                    endpoint=_PHAROS_MCP_SSE_URL,
                    query={"gene_symbol": request.gene_symbol},
                ),
            )
        )

        # Records 2..K: disease association entries when available.
        for idx, disease in enumerate(chosen_diseases[: max(0, top_k - 1)], start=1):
            d_score = float(disease.get("score") or 0.0)
            d_name = disease.get("name")
            d_mondo = disease.get("mondo_id")
            d_conf = min(0.95, 0.5 + min(0.35, d_score * 0.4))
            records.append(
                EvidenceRecord(
                    source=SourceName.PHAROS,
                    target_id=f"{target.get('sym') or request.gene_symbol}:{d_mondo or idx}",
                    target_symbol=target.get("sym") or request.gene_symbol,
                    disease_id=d_mondo,
                    evidence_type="disease_association",
                    raw_value=d_score,
                    normalized_score=min(1.0, max(0.0, d_score)),
                    confidence=min(0.95, max(0.0, d_conf)),
                    support={
                        "rank": idx,
                        "target_tdl": tdl,
                        "disease_name": d_name,
                        "mondo_id": d_mondo,
                        "source": "Community Pharos MCP (Cloudflare Workers)",
                    },
                    summary=(
                        f"[ext] Pharos disease association rank {idx}: "
                        f"{request.gene_symbol} ↔ {d_name or d_mondo}, score={d_score:.3f}."
                    ),
                    provenance=Provenance(
                        provider="PHAROS (community MCP)",
                        endpoint=_PHAROS_MCP_SSE_URL,
                        query={"gene_symbol": request.gene_symbol, "disease_id": d_mondo},
                    ),
                )
            )

        ms = int((time.perf_counter() - started_at) * 1000)
        status = SourceStatus(source=SourceName.PHAROS, status=StatusName.SUCCESS, duration_ms=ms, record_count=len(records))
        return records, status, []

    except Exception as exc:  # noqa: BLE001
        ms = int((time.perf_counter() - started_at) * 1000)
        msg = f"[ext_pharos] Error connecting to Pharos MCP at {_PHAROS_MCP_SSE_URL}: {exc}"
        status = SourceStatus(source=SourceName.PHAROS, status=StatusName.FAILED, duration_ms=ms, record_count=0, error_code=ErrorCode.UPSTREAM_ERROR, error_message=msg)
        return [], status, []


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="ext_pharos_collect_target_evidence",
    description="Collect target annotation evidence from the community Pharos MCP server (SSE on :8787).",
)
async def ext_pharos_collect_target_evidence(params: SourceCollectInput) -> str | dict:
    """Retrieve Pharos evidence via the community Pharos MCP Cloudflare Workers server."""
    request = build_request(params, SourceName.PHAROS)
    items, source_status, errors = await _fetch_evidence(request)
    result = CollectorResult(
        run_id=request.run_id,
        query=request,
        items=items,
        source_status=[source_status],
        errors=errors,
    )
    return format_source_result(result, SourceName.PHAROS, params.response_format)


if __name__ == "__main__":
    mcp.run(transport="stdio")
