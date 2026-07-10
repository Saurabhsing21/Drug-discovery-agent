from __future__ import annotations

import asyncio

from drugagent.core.schema import CollectorRequest, CollectorResult, ErrorRecord, EvidenceRecord, SourceStatus
from drugagent.data.connectors import CollectorConnector, get_default_connectors


async def collect_evidence_bundle(
    request: CollectorRequest,
    connectors: dict[str, CollectorConnector] | None = None,
) -> CollectorResult:
    active_connectors = connectors or get_default_connectors()

    selected_keys = [source.value if hasattr(source, "value") else str(source) for source in request.sources]
    tasks = []

    for source_key in selected_keys:
        connector = active_connectors.get(source_key)
        if connector is None:
            continue
        tasks.append(connector.collect(request))

    gathered = await asyncio.gather(*tasks) if tasks else []

    all_items: list[EvidenceRecord] = []
    all_status: list[SourceStatus] = []
    all_errors: list[ErrorRecord] = []

    for items, status, errors in gathered:
        all_items.extend(items)
        all_status.append(status)
        all_errors.extend(errors)

    return CollectorResult(
        run_id=request.run_id,
        query=request,
        items=all_items,
        source_status=all_status,
        errors=all_errors,
    )
