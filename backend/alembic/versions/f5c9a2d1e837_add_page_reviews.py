"""add page_reviews + page_review_recommendations

Revision ID: f5c9a2d1e837
Revises: f3b2a0e4c819
Create Date: 2026-04-17 20:00:00.000000

Module 3 — Page Review storage.

page_reviews holds one row per (page, composite_hash, reviewer_version).
composite_hash = sha256(content_hash + title + meta_description + h1) so
any user-visible change invalidates the idempotency key.

page_review_recommendations holds N findings per review with user_status
tracking so the UI can mark rows as applied/dismissed/deferred.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f5c9a2d1e837'
down_revision: Union[str, None] = 'f3b2a0e4c819'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'page_reviews',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('page_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('coverage_decision_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('target_intent_code', sa.String(30), nullable=False),
        sa.Column('composite_hash', sa.String(64), nullable=False),
        sa.Column('reviewer_model', sa.String(50), nullable=False, server_default='python-only'),
        sa.Column('reviewer_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('skip_reason', sa.String(40), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('cost_usd', sa.Numeric(8, 6), nullable=False, server_default='0'),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('top_queries_snapshot', postgresql.JSONB, nullable=True),
        sa.Column('page_level_summary', postgresql.JSONB, nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['coverage_decision_id'], ['coverage_decisions.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('page_id', 'composite_hash', 'reviewer_version', name='uq_page_reviews_page_hash_version'),
    )
    op.create_index('ix_page_reviews_page_id', 'page_reviews', ['page_id'])
    op.create_index('ix_page_reviews_site_id', 'page_reviews', ['site_id'])
    op.create_index('ix_page_reviews_composite_hash', 'page_reviews', ['composite_hash'])
    op.create_index(
        'ix_page_reviews_site_status_reviewed',
        'page_reviews',
        ['site_id', 'status', sa.text('reviewed_at DESC')],
    )

    op.create_table(
        'page_review_recommendations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('review_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category', sa.String(30), nullable=False),
        sa.Column('priority', sa.String(10), nullable=False),
        sa.Column('before_text', sa.Text(), nullable=True),
        sa.Column('after_text', sa.Text(), nullable=True),
        sa.Column('reasoning_ru', sa.Text(), nullable=False),
        sa.Column('estimated_impact', postgresql.JSONB, nullable=True),
        sa.Column('user_status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('user_status_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_status_changed_by', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['review_id'], ['page_reviews.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_page_review_recs_review_cat', 'page_review_recommendations', ['review_id', 'category'])
    op.create_index('ix_page_review_recs_site_user_status', 'page_review_recommendations', ['site_id', 'user_status'])

    # Enum guards (CHECK constraints) so bad string values never land in DB.
    op.create_check_constraint(
        'ck_page_reviews_status',
        'page_reviews',
        "status IN ('pending','in_progress','completed','failed','skipped')",
    )
    op.create_check_constraint(
        'ck_page_reviews_skip_reason',
        'page_reviews',
        "skip_reason IS NULL OR skip_reason IN ("
        "'unchanged_hash','not_strengthen','missing_content',"
        "'no_profile_rules','page_deleted','no_fingerprint','over_budget_cap')",
    )
    op.create_check_constraint(
        'ck_page_review_recs_category',
        'page_review_recommendations',
        "category IN ("
        "'title','meta_description','h1_structure','schema','eeat',"
        "'commercial','over_optimization','internal_linking')",
    )
    op.create_check_constraint(
        'ck_page_review_recs_priority',
        'page_review_recommendations',
        "priority IN ('critical','high','medium','low')",
    )
    op.create_check_constraint(
        'ck_page_review_recs_user_status',
        'page_review_recommendations',
        "user_status IN ('pending','applied','dismissed','deferred')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_page_review_recs_user_status', 'page_review_recommendations')
    op.drop_constraint('ck_page_review_recs_priority', 'page_review_recommendations')
    op.drop_constraint('ck_page_review_recs_category', 'page_review_recommendations')
    op.drop_constraint('ck_page_reviews_skip_reason', 'page_reviews')
    op.drop_constraint('ck_page_reviews_status', 'page_reviews')

    op.drop_index('ix_page_review_recs_site_user_status', table_name='page_review_recommendations')
    op.drop_index('ix_page_review_recs_review_cat', table_name='page_review_recommendations')
    op.drop_table('page_review_recommendations')

    op.drop_index('ix_page_reviews_site_status_reviewed', table_name='page_reviews')
    op.drop_index('ix_page_reviews_composite_hash', table_name='page_reviews')
    op.drop_index('ix_page_reviews_site_id', table_name='page_reviews')
    op.drop_index('ix_page_reviews_page_id', table_name='page_reviews')
    op.drop_table('page_reviews')
