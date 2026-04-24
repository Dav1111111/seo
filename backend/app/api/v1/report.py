"""Module 5 API — weekly reports."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.dto import WeeklyReport as WeeklyReportDTO
from app.core_audit.report.renderers.markdown import render_markdown
from app.core_audit.report.service import ReportService
from app.database import get_db

router = APIRouter()


class QueuedResponse(BaseModel):
    task_id: str
    status: str
    run_id: str | None = None


@router.post("/reports/sites/{site_id}/run", response_model=QueuedResponse)
async def trigger_report(site_id: uuid.UUID, week_end: str | None = None):
    from app.core_audit.report.tasks import report_build_site
    run_id = str(uuid.uuid4())
    task = report_build_site.delay(str(site_id), week_end, run_id=run_id)
    return QueuedResponse(task_id=task.id, status="queued", run_id=run_id)


@router.get("/reports/sites/{site_id}/latest")
async def get_latest(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await ReportService().get_latest(db, site_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no reports for site")
    return _row_dto(row)


@router.get("/reports/{report_id}")
async def get_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await ReportService().get(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="report not found")
    return _row_dto(row)


@router.get("/reports/{report_id}/markdown", response_class=Response)
async def get_report_markdown(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = await ReportService().get(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="report not found")
    dto = WeeklyReportDTO.model_validate(row.payload)
    body = render_markdown(dto)
    return Response(content=body, media_type="text/markdown; charset=utf-8")


@router.get("/reports/sites/{site_id}")
async def list_reports(
    site_id: uuid.UUID, limit: int = 20, db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await ReportService().list_for_site(db, site_id, limit=limit)
    return {"total": len(rows), "items": [_row_summary(r) for r in rows]}


def _row_dto(r) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "site_id": str(r.site_id),
        "week_start": r.week_start.isoformat(),
        "week_end": r.week_end.isoformat(),
        "builder_version": r.builder_version,
        "status": r.status,
        "health_score": r.health_score,
        "llm_cost_usd": float(r.llm_cost_usd or 0.0),
        "generation_ms": r.generation_ms,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        "payload": r.payload,
    }


def _row_summary(r) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "week_start": r.week_start.isoformat(),
        "week_end": r.week_end.isoformat(),
        "status": r.status,
        "health_score": r.health_score,
        "llm_cost_usd": float(r.llm_cost_usd or 0.0),
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
    }
