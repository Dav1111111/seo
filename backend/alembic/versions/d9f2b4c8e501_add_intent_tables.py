"""add intent tables

Revision ID: d9f2b4c8e501
Revises: c8a1e4f9d702
Create Date: 2026-04-17 15:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd9f2b4c8e501'
down_revision: Union[str, None] = 'c8a1e4f9d702'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # query_intents — 1:1 with search_queries
    op.create_table(
        'query_intents',
        sa.Column('query_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('intent_code', sa.String(30), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('matched_pattern', sa.Text(), nullable=True),
        sa.Column('is_brand', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_ambiguous', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('classifier_source', sa.String(20), nullable=False, server_default='regex'),
        sa.Column('classifier_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('classified_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['query_id'], ['search_queries.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.PrimaryKeyConstraint('query_id'),
    )
    op.create_index('ix_query_intents_site_id', 'query_intents', ['site_id'])
    op.create_index('ix_query_intents_intent_code', 'query_intents', ['intent_code'])
    op.create_index('ix_query_intents_is_ambiguous', 'query_intents', ['is_ambiguous'])

    # page_intent_scores — N rows per page (one per intent category)
    op.create_table(
        'page_intent_scores',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('page_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('intent_code', sa.String(30), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('s1_heading', sa.Float(), server_default='0.0'),
        sa.Column('s2_content', sa.Float(), server_default='0.0'),
        sa.Column('s3_structure', sa.Float(), server_default='0.0'),
        sa.Column('s4_cta', sa.Float(), server_default='0.0'),
        sa.Column('s5_schema', sa.Float(), server_default='0.0'),
        sa.Column('s6_eeat', sa.Float(), server_default='0.0'),
        sa.Column('scorer_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('scored_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_id', 'intent_code', name='uq_page_intent'),
    )
    op.create_index('ix_page_intent_score', 'page_intent_scores', ['site_id', 'intent_code', 'score'])

    # coverage_decisions
    op.create_table(
        'coverage_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('intent_code', sa.String(30), nullable=False),
        sa.Column('cluster_key', sa.String(255), nullable=False),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('coverage_status', sa.String(20), nullable=False),
        sa.Column('justification_ru', sa.Text(), nullable=True),
        sa.Column('target_page_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('proposed_url', sa.String(2048), nullable=True),
        sa.Column('queries_in_cluster', sa.Integer(), server_default='0'),
        sa.Column('total_impressions', sa.Integer(), server_default='0'),
        sa.Column('expected_lift_impressions', sa.Integer(), nullable=True),
        sa.Column('evidence', postgresql.JSONB(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_coverage_site', 'coverage_decisions', ['site_id'])
    op.create_index('ix_coverage_site_status', 'coverage_decisions', ['site_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_coverage_site_status', table_name='coverage_decisions')
    op.drop_index('ix_coverage_site', table_name='coverage_decisions')
    op.drop_table('coverage_decisions')

    op.drop_index('ix_page_intent_score', table_name='page_intent_scores')
    op.drop_table('page_intent_scores')

    op.drop_index('ix_query_intents_is_ambiguous', table_name='query_intents')
    op.drop_index('ix_query_intents_intent_code', table_name='query_intents')
    op.drop_index('ix_query_intents_site_id', table_name='query_intents')
    op.drop_table('query_intents')
