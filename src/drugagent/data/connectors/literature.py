from __future__ import annotations

import os
import time
import re

import httpx

from drugagent.core.schema import CollectorRequest, ErrorCode, EvidenceRecord, Provenance, SourceName

from .base import CollectorConnector
from .http_client import JsonHttpClient


class LiteratureConnector(CollectorConnector):
    source = SourceName.LITERATURE

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "EUROPE_PMC_SEARCH_URL",
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        )
        self.http = JsonHttpClient(timeout_s=20.0, retries=2)

    @staticmethod
    def _build_query(gene_symbol: str, disease_id: str | None) -> str:
        terms = [f'"{gene_symbol}"']

        if not disease_id:
            return " AND ".join(terms)

        # Handle ontology-like IDs such as EFO_0000311 by also trying EFO:0000311.
        alt = None
        if __import__("re").match(r"^[A-Za-z]+_\d+$", disease_id):
            alt = disease_id.replace("_", ":", 1)

        if alt:
            terms.append(f'("{disease_id}" OR "{alt}")')
        else:
            terms.append(f'"{disease_id}"')

        return " AND ".join(terms)

    async def collect(self, request: CollectorRequest):
        started_at = time.perf_counter()

        query_string = self._build_query(request.gene_symbol, request.disease_id)
        fetch_n = min(
            100,
            max(
                int(request.max_literature_articles),
                int(request.per_source_top_k) * 5,
                25,
            ),
        )

        try:
            payload = await self.http.get_json(
                self.base_url,
                params={
                    "query": query_string,
                    "format": "json",
                    "pageSize": fetch_n,
                    "resultType": "core",
                    "sort": "CITED desc",
                },
            )
        except httpx.TimeoutException:
            message = "Literature search timed out"
            return [], self.error_status(started_at, ErrorCode.TIMEOUT, message), [
                self.error_record(ErrorCode.TIMEOUT, message, retryable=True)
            ]
        except Exception as exc:  # noqa: BLE001
            code = self.upstream_error_code(exc)
            message = f"Literature source error: {exc}"
            return [], self.error_status(started_at, code, message), [
                self.error_record(code, message, retryable=code in {ErrorCode.TIMEOUT, ErrorCode.RATE_LIMIT})
            ]

        results = payload.get("resultList", {}).get("result", [])
        if not results:
            # "Definitive zero": query succeeded but returned 0 hits.
            # Emit a single record so downstream scoring can treat this as informative absence.
            absence_records = [
                EvidenceRecord(
                    source=self.source,
                    target_id=f"{request.gene_symbol}:LIT:ABSENCE",
                    target_symbol=request.gene_symbol,
                    disease_id=request.disease_id,
                    evidence_type="literature_absence",
                    raw_value=0.0,
                    normalized_score=0.0,
                    confidence=self.safe_float(0.6),
                    support={
                        "absence": True,
                        "article_count_returned": 0,
                        "fetch_page_size": fetch_n,
                        "total_hit_count": 0,
                        "query": query_string,
                    },
                    summary=f"Europe PMC returned 0 hits for query: {query_string}",
                    provenance=Provenance(
                        provider="Europe PMC",
                        endpoint=self.base_url,
                        query={"query": query_string, "disease_id": request.disease_id},
                    ),
                )
            ]
            return absence_records, self.success_status(started_at, len(absence_records)), []

        def _has_gene_in_title(paper: dict) -> bool:
            title = str(paper.get("title") or "")
            if not title:
                return False
            # Prefer a whole-word match for the symbol to de-emphasize generic method papers
            # that only mention the target in the abstract/metadata.
            return bool(re.search(rf"\b{re.escape(request.gene_symbol)}\b", title, flags=re.IGNORECASE))

        def _has_gene_in_abstract(paper: dict) -> bool:
            abstract = str(paper.get("abstractText") or "")
            if not abstract:
                return False
            return bool(re.search(rf"\b{re.escape(request.gene_symbol)}\b", abstract, flags=re.IGNORECASE))

        eligible = [paper for paper in results if _has_gene_in_title(paper) or _has_gene_in_abstract(paper)]
        eligible_hit_count = len(eligible)
        total_hits = int(payload.get("hitCount") or len(results))

        if eligible_hit_count == 0:
            absence_records = [
                EvidenceRecord(
                    source=self.source,
                    target_id=f"{request.gene_symbol}:LIT:ABSENCE",
                    target_symbol=request.gene_symbol,
                    disease_id=request.disease_id,
                    evidence_type="literature_absence",
                    raw_value=0.0,
                    normalized_score=0.0,
                    confidence=self.safe_float(0.6),
                    support={
                        "absence": True,
                        "article_count_returned": 0,
                        "fetch_page_size": fetch_n,
                        "total_hit_count": total_hits,
                        "eligible_hit_count": 0,
                        "query": query_string,
                    },
                    summary=f"No eligible articles for {request.gene_symbol} in title or abstract.",
                    provenance=Provenance(
                        provider="Europe PMC",
                        endpoint=self.base_url,
                        query={"query": query_string, "disease_id": request.disease_id},
                    ),
                )
            ]
            return absence_records, self.success_status(started_at, len(absence_records)), []

        # Europe PMC sorting by citations is useful but can over-select generic "toolkit" papers.
        # Re-rank locally to boost papers where the target appears in the title (higher topicality),
        # then break ties by citations (impact).
        reranked = sorted(
            enumerate(eligible, start=1),
            key=lambda pair: (
                1 if _has_gene_in_title(pair[1]) else 0,
                int(pair[1].get("citedByCount") or 0),
                int(pair[1].get("pubYear") or 0),
                -pair[0],  # stable-ish tie-breaker, prefer earlier ranks from upstream
            ),
            reverse=True,
        )

        limit = min(
            len(reranked),
            int(request.per_source_top_k),
            int(request.max_literature_articles),
        )
        selected = reranked[:limit]
        records: list[EvidenceRecord] = []

        for idx, (orig_rank, paper) in enumerate(selected, start=1):
            citations = int(paper.get("citedByCount") or 0)
            confidence = min(0.95, 0.5 + min(citations / 200, 0.25) + min((limit - idx + 1) / 20, 0.1))
            normalized = min(1.0, 0.4 + min(citations / 150, 0.4) + min((limit - idx + 1) / 20, 0.2))
            pmid = paper.get("pmid")
            title = paper.get("title")
            pub_year = paper.get("pubYear")

            records.append(
                EvidenceRecord(
                    source=self.source,
                    target_id=f"{request.gene_symbol}:PMID:{pmid or idx}",
                    target_symbol=request.gene_symbol,
                    disease_id=request.disease_id,
                    evidence_type="literature_article",
                    raw_value=float(citations),
                    normalized_score=self.safe_float(normalized),
                    confidence=self.safe_float(confidence),
                    support={
                        "rank": idx,
                        "article_count_returned": limit,
                        "fetch_page_size": fetch_n,
                        "total_hit_count": total_hits,
                        "eligible_hit_count": eligible_hit_count,
                        "pmid": pmid,
                        "title": title,
                        "journal": paper.get("journalTitle"),
                        "pub_year": pub_year,
                        "cited_by_count": citations,
                        "gene_in_title": _has_gene_in_title(paper),
                        "gene_in_abstract": _has_gene_in_abstract(paper),
                        "original_rank_by_cited_sort": orig_rank,
                        "query": query_string,
                    },
                    summary=(
                        f"Europe PMC article rank {idx}/{limit} for {request.gene_symbol}: "
                        f"{title or 'untitled'} (PMID={pmid or 'n/a'}, citations={citations})."
                    ),
                    provenance=Provenance(
                        provider="Europe PMC",
                        endpoint=self.base_url,
                        query={"query": query_string, "disease_id": request.disease_id, "pmid": pmid},
                    ),
                )
            )

        return records, self.success_status(started_at, len(records)), []
