"""add target_demand_map tables + sites.target_config

Revision ID: c4d5e6f7a8b9
Revises: b7d8e9f0c1a2
Create Date: 2026-04-17 23:00:00.000000

Phase A of the Target Demand Map feature. Adds:
  - target_clusters  : deterministic Cartesian outputs per site
  - target_queries   : cluster -> query candidate (Phase C fills this)
  - sites.target_config (JSONB) : user-declared services/geo/brands

NO downstream consumer reads these yet. Safe to apply and roll back.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b7d8e9f0c1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. sites.target_config -----------------------------------------------
    op.add_column(
        'sites',
        sa.Column(
            'target_config',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # 2. target_clusters ---------------------------------------------------
    op.create_table(
        'target_clusters',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cluster_key', sa.String(128), nullable=False),
        sa.Column('name_ru', sa.String(500), nullable=False),
        sa.Column('intent_code', sa.String(30), nullable=False),
        sa.Column('cluster_type', sa.String(30), nullable=False),
        sa.Column('quality_tier', sa.String(20), nullable=False),
        sa.Column('keywords', postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('seed_slots', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_brand', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_competitor_brand', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('expected_volume_tier', sa.String(5), nullable=False, server_default='s'),
        sa.Column('business_relevance', sa.Numeric(4, 3), nullable=False, server_default='0'),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('site_id', 'cluster_key', name='uq_target_clusters_site_key'),
    )
    op.create_index(
        'ix_target_clusters_site_tier',
        'target_clusters',
        ['site_id', 'quality_tier'],
    )
    op.create_index(
        'ix_target_clusters_site_intent',
        'target_clusters',
        ['site_id', 'intent_code'],
    )
    op.create_check_constraint(
        'ck_target_clusters_cluster_type',
        'target_clusters',
        "cluster_type IN ('commercial_core','commercial_modifier','local_geo',"
        "'informational_dest','informational_prep','transactional_book',"
        "'trust','seasonality','brand','competitor_brand','activity')",
    )
    op.create_check_constraint(
        'ck_target_clusters_quality_tier',
        'target_clusters',
        "quality_tier IN ('core','secondary','exploratory','discarded')",
    )
    op.create_check_constraint(
        'ck_target_clusters_volume_tier',
        'target_clusters',
        "expected_volume_tier IN ('xs','s','m','l','xl')",
    )
    op.create_check_constraint(
        'ck_target_clusters_source',
        'target_clusters',
        "source IN ('profile_seed','cartesian','llm','suggest','observed')",
    )

    # 3. target_queries ----------------------------------------------------
    op.create_table(
        'target_queries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('cluster_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('query_text', sa.String(500), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('estimated_volume_tier', sa.String(5), nullable=True, server_default='s'),
        sa.Column('observed_search_query_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(
            ['cluster_id'], ['target_clusters.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['observed_search_query_id'], ['search_queries.id'], ondelete='SET NULL'
        ),
        sa.UniqueConstraint('cluster_id', 'query_text', name='uq_target_queries_cluster_q'),
    )
    op.create_index(
        'ix_target_queries_cluster',
        'target_queries',
        ['cluster_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_target_queries_cluster', table_name='target_queries')
    op.drop_table('target_queries')

    op.drop_constraint('ck_target_clusters_source', 'target_clusters')
    op.drop_constraint('ck_target_clusters_volume_tier', 'target_clusters')
    op.drop_constraint('ck_target_clusters_quality_tier', 'target_clusters')
    op.drop_constraint('ck_target_clusters_cluster_type', 'target_clusters')
    op.drop_index('ix_target_clusters_site_intent', table_name='target_clusters')
    op.drop_index('ix_target_clusters_site_tier', table_name='target_clusters')
    op.drop_table('target_clusters')

    op.drop_column('sites', 'target_config')
