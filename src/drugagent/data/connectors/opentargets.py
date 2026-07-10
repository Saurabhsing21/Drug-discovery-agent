from __future__ import annotations

import os
import time
from typing import Any

from drugagent.core.schema import CollectorRequest, ErrorCode, EvidenceRecord, Provenance, SourceName

from .base import CollectorConnector
from .http_client import JsonHttpClient


class OpenTargetsConnector(CollectorConnector):
    source = SourceName.OPENTARGETS

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "OPEN_TARGETS_GRAPHQL_URL",
            "https://api.platform.opentargets.org/api/v4/graphql",
        )
        self.http = JsonHttpClient(timeout_s=20.0, retries=2)

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        data = await self.http.post_json(self.base_url, payload=payload)

        if data.get("errors"):
            message = data["errors"][0].get("message", "Open Targets GraphQL error")
            raise ValueError(message)

        return data.get("data", {})

    async def _resolve_target(self, symbol: str) -> tuple[str, str] | None:
        query = """
        query SearchTarget($queryString: String!) {
          search(queryString: $queryString, entityNames: ["target"]) {
            hits {
              id
              entity
              name
            }
          }
        }
        """
        data = await self._graphql(query, {"queryString": symbol})
        hits = data.get("search", {}).get("hits", [])

        if not hits:
            return None

        for hit in hits:
            if hit.get("name") == symbol:
                return hit.get("id"), hit.get("name")

        first = hits[0]
        return first.get("id"), first.get("name") or symbol

    async def collect(self, request: CollectorRequest):
        started_at = time.perf_counter()

        try:
            resolved = await self._resolve_target(request.gene_symbol)
            if not resolved:
                message = f"Target '{request.gene_symbol}' not found in Open Targets"
                return [], self.skipped_status(started_at, message), []

            ensembl_id, resolved_symbol = resolved

            query = """
            query TargetAssociations($ensemblId: String!) {
              target(ensemblId: $ensemblId) {
                id
                approvedSymbol
                associatedDiseases {
                  count
                  rows {
                    score
                    disease {
                      id
                      name
                    }
                  }
                }
              }
            }
            """
            data = await self._graphql(query, {"ensemblId": ensembl_id})
            target = data.get("target") or {}
            rows = target.get("associatedDiseases", {}).get("rows", [])

            if not rows:
                # "Definitive zero": target exists but no disease associations found.
                # Emit a single record with score=0.0 so downstream normalization/scoring can treat
                # this as an informative absence rather than "missing source".
                association_count = int(target.get("associatedDiseases", {}).get("count") or 0)
                confidence = 0.85
                absence_records = [
                    EvidenceRecord(
                        source=self.source,
                        target_id=target.get("id") or ensembl_id,
                        target_symbol=target.get("approvedSymbol") or resolved_symbol or request.gene_symbol,
                        disease_id=request.disease_id,
                        evidence_type="disease_association_absence",
                        raw_value=0.0,
                        normalized_score=0.0,
                        confidence=self.safe_float(confidence),
                        support={
                            "absence": True,
                            "evidence_count": association_count,
                            "requested_disease": request.disease_id,
                        },
                        summary=(
                            f"Open Targets returned no disease associations for {request.gene_symbol}; "
                            "treat association score as 0.0."
                        ),
                        provenance=Provenance(
                            provider="Open Targets",
                            endpoint=self.base_url,
                            query={"gene_symbol": request.gene_symbol, "ensembl_id": ensembl_id, "disease_id": request.disease_id},
                        ),
                    )
                ]
                return absence_records, self.success_status(started_at, len(absence_records)), []

            top_k = max(1, int(request.per_source_top_k))
            final_rows = []
            
            # If disease_id is requested, find it first
            if request.disease_id:
                match = next(
                    (
                        row
                        for row in rows
                        if (row.get("disease", {}).get("id") or "").lower() == request.disease_id.lower()
                    ),
                    None,
                )
                if match:
                    final_rows.append(match)
            
            # Fill remaining slots with top associations
            for row in rows:
                if len(final_rows) >= top_k:
                    break
                if row not in final_rows:
                    final_rows.append(row)

            records: list[EvidenceRecord] = []
            for selected_row in final_rows:
                score = float(selected_row.get("score") or 0.0)
                association_count = int(target.get("associatedDiseases", {}).get("count") or 0)
                confidence = min(0.95, max(0.2, 0.55 + (score * 0.3) + min(association_count / 2000, 0.1)))

                disease = selected_row.get("disease") or {}
                disease_id = disease.get("id")
                disease_name = disease.get("name")

                records.append(EvidenceRecord(
                    source=self.source,
                    target_id=target.get("id") or ensembl_id,
                    target_symbol=target.get("approvedSymbol") or resolved_symbol or request.gene_symbol,
                    disease_id=disease_id,
                    evidence_type="disease_association",
                    raw_value=score,
                    normalized_score=self.safe_float(score, default=0.0),
                    confidence=self.safe_float(confidence),
                    support={
                        "evidence_count": association_count,
                        "disease_name": disease_name,
                        "requested_disease": request.disease_id,
                    },
                    summary=(
                        f"Open Targets association score for {request.gene_symbol}"
                        f" and {disease_name or disease_id or 'disease association'} is {score:.3f}."
                    ),
                    provenance=Provenance(
                        provider="Open Targets",
                        endpoint=self.base_url,
                        query={"gene_symbol": request.gene_symbol, "ensembl_id": ensembl_id, "disease_id": request.disease_id},
                    ),
                ))

            if not records:
                 message = f"No suitable disease associations found for '{request.gene_symbol}'"
                 return [], self.skipped_status(started_at, message), []

            return records, self.success_status(started_at, len(records)), []

        except Exception as exc:  # noqa: BLE001
            code = self.upstream_error_code(exc)
            message = f"Open Targets request failed: {exc}"
            return [], self.error_status(started_at, code, message), [
                self.error_record(code, message, retryable=code in {ErrorCode.TIMEOUT, ErrorCode.RATE_LIMIT})
            ]
