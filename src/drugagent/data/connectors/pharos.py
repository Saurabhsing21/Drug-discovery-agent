from __future__ import annotations

import os
import time
from typing import Any

from drugagent.core.schema import CollectorRequest, ErrorCode, EvidenceRecord, Provenance, SourceName

from .base import CollectorConnector
from .http_client import JsonHttpClient


class PharosConnector(CollectorConnector):
    source = SourceName.PHAROS

    def __init__(self) -> None:
        self.base_url = os.getenv("PHAROS_GRAPHQL_URL", "https://pharos-api.ncats.io/graphql")
        self.http = JsonHttpClient(timeout_s=25.0, retries=2)

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        data = await self.http.post_json(self.base_url, payload=payload)
        if data.get("errors"):
            message = data["errors"][0].get("message", "PHAROS GraphQL error")
            raise ValueError(message)
        return data.get("data", {})

    def _tdl_score(self, tdl: str | None) -> float:
        mapping = {
            "Tclin": 0.95,
            "Tchem": 0.75,
            "Tbio":  0.55,
            "Tdark": 0.25,
        }
        return mapping.get(tdl or "", 0.45)

    async def collect(self, request: CollectorRequest):
        started_at = time.perf_counter()

        query = """
        query targetDetails($term: String!) {
          target(q: {sym: $term}) {
            sym
            name
            tdl
            fam
            novelty
            ligandCounts {
              name
              value
            }
            diseases {
              name
              mondoID
            }
          }
        }
        """
        try:
            data = await self._graphql(query, {"term": request.gene_symbol})
            target = data.get("target") or {}

            if not target or not target.get("sym"):
                message = f"Target '{request.gene_symbol}' not found in PHAROS"
                return [], self.skipped_status(started_at, message), []

            ligand_counts = target.get("ligandCounts") or []
            ligand_total = 0
            for item in ligand_counts:
                value = item.get("value")
                if isinstance(value, (int, float)):
                    ligand_total += int(value)

            tdl = target.get("tdl")
            base_score = self._tdl_score(tdl)
            ligand_bonus = min(0.2, ligand_total / 200)
            normalized_score = min(1.0, base_score + ligand_bonus)
            confidence = min(0.95, 0.6 + (0.1 if tdl else 0.0) + min(ligand_total / 500, 0.15))

            target_symbol = target.get("sym") or request.gene_symbol
            target_name = target.get("name")
            family = target.get("fam")
            novelty = target.get("novelty")

            # Get diseases for multiple records
            diseases = target.get("diseases") or []
            top_k = max(1, int(request.per_source_top_k))
            
            final_diseases = []
            if request.disease_id:
                # Try to find the specific requested disease
                match = next((d for d in diseases if (d.get("mondoID") or "").lower() == request.disease_id.lower()), None)
                if match:
                    final_diseases.append(match)
            
            for d in diseases:
                if len(final_diseases) >= top_k:
                    break
                if d not in final_diseases:
                    final_diseases.append(d)

            # If no diseases found but target exists, still return one generic annotation
            if not final_diseases:
                final_diseases = [{"name": "Generic Annotation", "mondoID": request.disease_id}]

            records: list[EvidenceRecord] = []
            for d in final_diseases:
                d_name = d.get("name")
                d_id = d.get("mondoID")
                
                records.append(EvidenceRecord(
                    source=self.source,
                    target_id=target_symbol,
                    target_symbol=target_symbol,
                    disease_id=d_id or request.disease_id,
                    evidence_type="target_annotation",
                    raw_value={
                        "tdl": tdl,
                        "ligand_total": ligand_total,
                        "novelty": novelty,
                        "disease_name": d_name,
                    },
                    normalized_score=self.safe_float(normalized_score),
                    confidence=self.safe_float(confidence),
                    support={
                        "target_name": target_name,
                        "family": family,
                        "tdl": tdl,
                        "novelty": novelty,
                        "ligand_total": ligand_total,
                        "associated_disease": d_name,
                    },
                    summary=(
                        f"PHAROS annotations for {target_symbol} relating to {d_name or 'target'}"
                        f" (TDL={tdl or 'unknown'}, ligands={ligand_total})."
                    ),
                    provenance=Provenance(
                        provider="PHAROS",
                        endpoint=self.base_url,
                        query={"gene_symbol": request.gene_symbol, "disease": d_name},
                    ),
                ))

            return records, self.success_status(started_at, len(records)), []

        except Exception as exc:  # noqa: BLE001
            code = self.upstream_error_code(exc)
            message = f"PHAROS request failed: {exc}"
            return [], self.error_status(started_at, code, message), [
                self.error_record(code, message, retryable=code in {ErrorCode.TIMEOUT, ErrorCode.RATE_LIMIT})
            ]
