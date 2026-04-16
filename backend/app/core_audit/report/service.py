"""ReportService — persistence (CRUD) for weekly reports."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.builder import BUILDER_VERSION, ReportBuilder
from app.core_audit.report.dto import WeeklyReport as WeeklyReportDTO
from app.core_audit.report.models import WeeklyReport

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, builder_version: str = BUILDER_VERSION) -> None:
        self.builder_version = builder_version

    async def build_and_save(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        week_end: date | None = None,
    ) -> WeeklyReport:
        builder = ReportBuilder(builder_version=self.builder_version)
        dto = await builder.build_weekly_report(db, site_id, week_end=week_end)
        return await self._persist(db, dto)

    async def _persist(self, db: AsyncSession, dto: WeeklyReportDTO) -> WeeklyReport:
        row = WeeklyReport(
            id=uuid.uuid4(),
            site_id=dto.meta.site_id,
            week_start=dto.meta.week_start,
            week_end=dto.meta.week_end,
            builder_version=dto.meta.builder_version,
            status=dto.meta.status,
            payload=dto.to_jsonb(),
            health_score=dto.executive.health_score,
            llm_cost_usd=float(dto.meta.llm_cost_usd or 0.0),
            generation_ms=dto.meta.generation_ms,
            generated_at=dto.meta.generated_at,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            # Same site_id+week_end+version already exists — overwrite status/payload
            await db.rollback()
            existing = (await db.execute(
                select(WeeklyReport).where(
                    WeeklyReport.site_id == dto.meta.site_id,
                    WeeklyReport.week_end == dto.meta.week_end,
                    WeeklyReport.builder_version == dto.meta.builder_version,
                )
            )).scalar_one()
            existing.payload = dto.to_jsonb()
            existing.status = dto.meta.status
            existing.health_score = dto.executive.health_score
            existing.llm_cost_usd = float(dto.meta.llm_cost_usd or 0.0)
            existing.generation_ms = dto.meta.generation_ms
            existing.generated_at = dto.meta.generated_at
            await db.commit()
            return existing
        return row

    async def get_latest(self, db: AsyncSession, site_id: UUID) -> WeeklyReport | None:
        row = await db.execute(
            select(WeeklyReport)
            .where(WeeklyReport.site_id == site_id)
            .order_by(WeeklyReport.week_end.desc(), WeeklyReport.generated_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def get(self, db: AsyncSession, report_id: UUID) -> WeeklyReport | None:
        row = await db.execute(select(WeeklyReport).where(WeeklyReport.id == report_id))
        return row.scalar_one_or_none()

    async def list_for_site(
        self, db: AsyncSession, site_id: UUID, limit: int = 20,
    ) -> list[WeeklyReport]:
        row = await db.execute(
            select(WeeklyReport)
            .where(WeeklyReport.site_id == site_id)
            .order_by(WeeklyReport.week_end.desc(), WeeklyReport.generated_at.desc())
            .limit(limit)
        )
        return list(row.scalars().all())
