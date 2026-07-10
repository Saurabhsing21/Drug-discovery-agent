"""Compare report router."""
from __future__ import annotations
from fastapi import APIRouter
from agents.compare_report_agent import CompareReportAgent
from api.models import CompareReportInput, CompareReportResponse

router = APIRouter()


@router.post("/api/compare-report", response_model=CompareReportResponse)
async def compare_report(body: CompareReportInput) -> CompareReportResponse:
    agent = CompareReportAgent(model=body.model_override)
    markdown = await agent.run(
        report_a=body.report_a,
        report_b=body.report_b,
        title_a=body.title_a,
        title_b=body.title_b,
    )
    return CompareReportResponse(markdown=markdown)
