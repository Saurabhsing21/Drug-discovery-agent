"""
MCP Runtime — calls MCP servers on behalf of the collector agent.

Internal sources  (DEPMAP, LITERATURE)  → our own mcp_server/* modules (stdio)
External sources  (OPENTARGETS, PHAROS)  → external MCP servers called DIRECTLY:
  • OPENTARGETS : external_mcps/open-targets-platform-mcp  (stdio, otp-mcp binary)
  • PHAROS      : public PHAROS GraphQL API                (https://pharos-api.ncats.io/graphql)
"""
from __future__ import annotations

import json
import os
import time
import asyncio
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from drugagent.core.schema import (
    CollectorRequest,
    ErrorCode,
    ErrorRecord,
    EvidenceRecord,
    SourceName,
    SourceStatus,
    StatusName,
)

# ─────────────────────────────────────────────────────────────────────────────
# MCP Source Mapping — route each SourceName to an MCP module + tool name
# ─────────────────────────────────────────────────────────────────────────────

_INTERNAL_SOURCE_MAP: dict[SourceName, tuple[str, str]] = {
    # 2 MCPs we have built (Internal Connectors)
    SourceName.DEPMAP:      ("drugagent.data.mcp_servers.depmap_mcp",     "depmap_collect_target_evidence"),
    SourceName.PHAROS:      ("drugagent.data.mcp_servers.pharos_mcp",     "pharos_collect_target_evidence"),
    SourceName.OPENTARGETS: ("drugagent.data.mcp_servers.opentargets_mcp", "opentargets_collect_target_evidence"),
    SourceName.LITERATURE:  ("drugagent.data.mcp_servers.literature_mcp", "literature_collect_target_evidence"),

    # External MCP wrappers
    SourceName.EXT_OPENTARGETS: ("drugagent.data.mcp_servers.ext_opentargets_mcp", "ext_opentargets_collect_target_evidence"),
    SourceName.EXT_PHAROS:  ("drugagent.data.mcp_servers.ext_pharos_mcp",      "ext_pharos_collect_target_evidence"),
}

# External MCP paths (used for binary/SSE if called directly, but preferred via the wrappers above)
_PROJECT_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OTP_BIN       = os.path.join(_PROJECT_ROOT, "external_mcps", "open-targets-platform-mcp", ".venv", "bin", "otp-mcp")
_OTP_HOME      = os.path.join(_PROJECT_ROOT, "external_mcps", "open-targets-platform-mcp")
_PHAROS_SSE    = os.getenv("EXT_PHAROS_SSE_URL", "http://127.0.0.1:8787/sse")

# For graph.py dynamic source discovery
_SOURCE_TO_MODULE_TOOL = _INTERNAL_SOURCE_MAP
MAX_RETRY_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 0.1
MAX_RETRY_DELAY_SECONDS = 0.4


# ─────────────────────────────────────────────────────────────────────────────
# Helpers shared by stdio MCP path
# ─────────────────────────────────────────────────────────────────────────────

def _server_params(source: SourceName) -> StdioServerParameters:
    module_name, _ = _INTERNAL_SOURCE_MAP[source]
    env = dict(os.environ)
    # Suppress FastMCP banners in the subprocesses
    env["FASTMCP_LOG_LEVEL"] = "ERROR"
    return StdioServerParameters(
        command=os.getenv("A4T_MCP_PYTHON_BIN", "python3"),
        args=["-m", module_name],
        env=env,
        cwd=_PROJECT_ROOT,
    )


def _tool_arguments(source: SourceName, request: CollectorRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "gene_symbol":   request.gene_symbol,
        "disease_id":    request.disease_id,
        "species":       request.species,
        "per_source_top_k": request.per_source_top_k,
        "response_format": "json",
        "run_id":        request.run_id,
    }
    if source == SourceName.LITERATURE:
        params["max_literature_articles"] = request.max_literature_articles
    return {"params": params}


def _error_code_from_message(message: str) -> ErrorCode:
    lo = message.lower()
    if "timed out" in lo or "timeout" in lo:
        return ErrorCode.TIMEOUT
    if "429" in lo or "rate limit" in lo:
        return ErrorCode.RATE_LIMIT
    if "not found" in lo:
        return ErrorCode.NOT_FOUND
    if "validation" in lo:
        return ErrorCode.VALIDATION_ERROR
    return ErrorCode.UPSTREAM_ERROR


def _is_retryable_error_code(code: ErrorCode) -> bool:
    return code in {
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMIT,
        ErrorCode.UPSTREAM_ERROR,
    }


def _retry_delay_seconds(attempt_index: int) -> float:
    return min(BASE_RETRY_DELAY_SECONDS * (2 ** attempt_index), MAX_RETRY_DELAY_SECONDS)


def _with_retry_telemetry(
    payload: dict[str, Any],
    request: CollectorRequest,
    source: SourceName,
    retry_events: list[dict[str, Any]],
    attempt_count: int,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched.setdefault("run_id", request.run_id)
    enriched.setdefault("source", source.value)
    enriched.setdefault("query", request.model_dump(mode="json"))
    enriched["retry_attempts"] = attempt_count
    enriched["retry_telemetry"] = retry_events
    return enriched


def _extract_payload(call_result: Any) -> dict[str, Any]:
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    text_parts = [
        getattr(c, "text", "")
        for c in getattr(call_result, "content", [])
        if getattr(c, "type", None) == "text"
    ]
    text = "\n".join(p for p in text_parts if p)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _parse_payload_models(
    source: SourceName,
    payload: dict[str, Any],
) -> tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord]]:
    items: list[EvidenceRecord] = []
    for item in payload.get("items", []):
        try:
            items.append(EvidenceRecord.model_validate(item))
        except Exception:
            # Keep runtime resilient: summary agent can still use raw MCP payload directly.
            continue
    source_status_entries = [
        SourceStatus.model_validate(e)
        for e in payload.get("source_status", [])
        if str(e.get("source", "")).lower() == source.value
    ]
    errors = [
        ErrorRecord.model_validate(e)
        for e in payload.get("errors", [])
        if str(e.get("source", "")).lower() == source.value
    ]
    if source_status_entries:
        status = source_status_entries[0]
    else:
        status_from_payload = None
        for entry in payload.get("source_status", []):
            if str(entry.get("source", "")).lower() == source.value:
                try:
                    status_from_payload = SourceStatus.model_validate(entry)
                    break
                except Exception:
                    status_from_payload = None
        if status_from_payload is not None:
            status = status_from_payload
        else:
            status = SourceStatus(
                source=source,
                status=StatusName.SUCCESS if items else StatusName.SKIPPED,
                duration_ms=0,
                record_count=len(items),
                error_message=None if items else "No source status returned",
            )
    return items, status, errors


# ─────────────────────────────────────────────────────────────────────────────
# Main dispatcher — called by graph.py for every requested source
# ─────────────────────────────────────────────────────────────────────────────

async def collect_source_via_mcp(
    source: SourceName,
    request: CollectorRequest,
) -> tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord]]:
    """Backward-compatible wrapper that ignores raw payload."""
    items, status, errors, _ = await collect_source_via_mcp_with_raw(source, request)
    return items, status, errors


async def _execute_mcp_call_once(
    source: SourceName,
    request: CollectorRequest,
) -> tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord], dict[str, Any]]:
    """Execute a single MCP call attempt for a source."""

    if source not in _INTERNAL_SOURCE_MAP:
        msg = f"Unknown source: {source.value}"
        status = SourceStatus(source=source, status=StatusName.FAILED,
                              duration_ms=0, record_count=0,
                              error_code=ErrorCode.INTERNAL_ERROR, error_message=msg)
        errors = [ErrorRecord(source=source, error_code=ErrorCode.INTERNAL_ERROR, message=msg, retryable=False)]
        raw_payload = {
            "run_id": request.run_id,
            "source": source.value,
            "query": request.model_dump(mode="json"),
            "items": [],
            "source_status": [status.model_dump(mode="json")],
            "errors": [e.model_dump(mode="json") for e in errors],
        }
        return [], status, errors, raw_payload

    started_at = time.perf_counter()
    _, tool_name = _INTERNAL_SOURCE_MAP[source]

    try:
        server_params = _server_params(source)
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, _tool_arguments(source, request))

        if result.isError:
            message = "MCP tool returned error"
            text_parts = [
                getattr(c, "text", "")
                for c in getattr(result, "content", [])
                if getattr(c, "type", None) == "text"
            ]
            combined = " | ".join(p for p in text_parts if p)
            if combined:
                message = combined
            code = _error_code_from_message(message)
            status = SourceStatus(
                source=source, status=StatusName.FAILED,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                record_count=0, error_code=code, error_message=message,
            )
            errors = [ErrorRecord(source=source, error_code=code, message=message, retryable=_is_retryable_error_code(code))]
            raw_payload = {
                "run_id": request.run_id,
                "source": source.value,
                "query": request.model_dump(mode="json"),
                "items": [],
                "source_status": [status.model_dump(mode="json")],
                "errors": [e.model_dump(mode="json") for e in errors],
            }
            return [], status, errors, raw_payload

        payload = _extract_payload(result)
        items, status, errors = _parse_payload_models(source, payload)

        if status.duration_ms <= 0:
            status.duration_ms = int((time.perf_counter() - started_at) * 1000)
        if status.record_count == 0 and items:
            status.record_count = len(items)

        raw_payload = payload or {
            "run_id": request.run_id,
            "source": source.value,
            "query": request.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in items],
            "source_status": [status.model_dump(mode="json")],
            "errors": [e.model_dump(mode="json") for e in errors],
        }
        return items, status, errors, raw_payload

    except Exception as exc:  # noqa: BLE001
        message = f"MCP execution failed for {source.value}: {exc}"
        code = _error_code_from_message(message)
        status = SourceStatus(
            source=source, status=StatusName.FAILED,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            record_count=0, error_code=code, error_message=message,
        )
        errors = [ErrorRecord(source=source, error_code=code, message=message, retryable=_is_retryable_error_code(code))]
        raw_payload = {
            "run_id": request.run_id,
            "source": source.value,
            "query": request.model_dump(mode="json"),
            "items": [],
            "source_status": [status.model_dump(mode="json")],
            "errors": [e.model_dump(mode="json") for e in errors],
        }
        return [], status, errors, raw_payload


async def collect_source_via_mcp_with_raw(
    source: SourceName,
    request: CollectorRequest,
) -> tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord], dict[str, Any]]:
    """Route to designated MCP module based on source with bounded retry/backoff."""

    retry_events: list[dict[str, Any]] = []
    last_result: tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord], dict[str, Any]] | None = None

    for attempt_index in range(MAX_RETRY_ATTEMPTS):
        items, status, errors, raw_payload = await _execute_mcp_call_once(source, request)
        last_result = (items, status, errors, raw_payload)

        is_retryable_failure = (
            status.status == StatusName.FAILED
            and status.error_code is not None
            and _is_retryable_error_code(status.error_code)
            and attempt_index < MAX_RETRY_ATTEMPTS - 1
        )
        if not is_retryable_failure:
            return items, status, errors, _with_retry_telemetry(
                raw_payload,
                request=request,
                source=source,
                retry_events=retry_events,
                attempt_count=attempt_index + 1,
            )

        delay_seconds = _retry_delay_seconds(attempt_index)
        error_code = status.error_code
        assert error_code is not None
        retry_events.append(
            {
                "attempt": attempt_index + 1,
                "error_code": error_code.value if hasattr(error_code, "value") else str(error_code),
                "message": status.error_message,
                "delay_ms": int(delay_seconds * 1000),
            }
        )
        await asyncio.sleep(delay_seconds)

    assert last_result is not None
    items, status, errors, raw_payload = last_result
    return items, status, errors, _with_retry_telemetry(
        raw_payload,
        request=request,
        source=source,
        retry_events=retry_events,
        attempt_count=MAX_RETRY_ATTEMPTS,
    )
