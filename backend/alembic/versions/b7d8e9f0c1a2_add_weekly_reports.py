"""add weekly_reports table

Revision ID: b7d8e9f0c1a2
Revises: a12b3c4d5e6f
Create Date: 2026-04-17 22:30:00.000000

Module 5 — single JSONB payload per (site, week_end, builder_version).
Top-level health_score column for cheap trend queries.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b7d8e9f0c1a2'
down_revision: Union[str, None] = 'a12b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'weekly_reports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('week_end', sa.Date(), nullable=False),
        sa.Column('builder_version', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='completed'),
        sa.Column('payload', postgresql.JSONB, nullable=False),
        sa.Column('health_score', sa.Integer(), nullable=True),
        sa.Column('llm_cost_usd', sa.Numeric(8, 6), nullable=False, server_default='0'),
        sa.Column('generation_ms', sa.Integer(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('site_id', 'week_end', 'builder_version', name='uq_weekly_reports_site_week_version'),
    )
    op.create_index('ix_weekly_reports_site_week', 'weekly_reports', ['site_id', 'week_end'])
    op.create_index('ix_weekly_reports_health_score', 'weekly_reports', ['health_score'])
    op.create_check_constraint(
        'ck_weekly_reports_status',
        'weekly_reports',
        "status IN ('queued','running','completed','failed','draft')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_weekly_reports_status', 'weekly_reports')
    op.drop_index('ix_weekly_reports_health_score', table_name='weekly_reports')
    op.drop_index('ix_weekly_reports_site_week', table_name='weekly_reports')
    op.drop_table('weekly_reports')
