"""add page_fingerprints table

Revision ID: c8a1e4f9d702
Revises: a7b3f2e1d5c6
Create Date: 2026-04-17 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c8a1e4f9d702'
down_revision: Union[str, None] = 'a7b3f2e1d5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'page_fingerprints',
        sa.Column('page_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('site_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('normalized_url', sa.String(2048), nullable=False),

        # Extraction metadata
        sa.Column('content_text_length', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_language', sa.String(5), nullable=False, server_default='ru'),
        sa.Column('main_content_extracted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('extraction_status', sa.String(20), nullable=False, server_default='ok'),
        sa.Column('extraction_error', sa.Text(), nullable=True),

        # Core fingerprint payload
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('minhash_signature', sa.LargeBinary(), nullable=True),
        sa.Column('minhash_num_perm', sa.SmallInteger(), nullable=False, server_default='128'),
        sa.Column('shingle_size', sa.SmallInteger(), nullable=False, server_default='5'),
        sa.Column('ngram_hash_vector', sa.LargeBinary(), nullable=True),
        sa.Column('ngram_n_features', sa.Integer(), nullable=False, server_default='262144'),
        sa.Column('ngram_ngram_range', sa.String(10), nullable=False, server_default='3,5'),
        sa.Column('ngram_format_version', sa.String(20), nullable=False, server_default='v1'),
        sa.Column('title_normalized', sa.Text(), nullable=True),
        sa.Column('h1_normalized', sa.Text(), nullable=True),

        # Metrics
        sa.Column('content_length_chars', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_length_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('boilerplate_ratio', sa.Float(), nullable=False, server_default='0.0'),

        # Versioning
        sa.Column('extraction_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('lemmatization_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('minhash_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('ngram_version', sa.String(20), nullable=False, server_default='1.0.0'),
        sa.Column('fingerprint_schema_version', sa.String(20), nullable=False, server_default='1.0.0'),

        # Lifecycle
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('skip_reason', sa.String(30), nullable=True),
        sa.Column('last_status_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Timing
        sa.Column('source_crawl_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_fingerprinted_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.PrimaryKeyConstraint('page_id'),

        # CHECKs
        sa.CheckConstraint(
            "fingerprint_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name='ck_fingerprint_schema_version_semver',
        ),
        sa.CheckConstraint(
            'minhash_num_perm IN (64, 128, 256)',
            name='ck_fingerprint_minhash_num_perm',
        ),
        sa.CheckConstraint(
            'shingle_size BETWEEN 2 AND 10',
            name='ck_fingerprint_shingle_size',
        ),
        sa.CheckConstraint(
            'boilerplate_ratio BETWEEN 0 AND 1',
            name='ck_fingerprint_boilerplate_ratio',
        ),
        sa.CheckConstraint(
            "status IN ('pending','extracted','fingerprinted','skipped_unchanged',"
            "'skipped_thin','skipped_unsupported','failed')",
            name='ck_fingerprint_status',
        ),
        sa.CheckConstraint(
            "extraction_status IN ('ok','fallback_raw','failed')",
            name='ck_fingerprint_extraction_status',
        ),
    )

    # Indexes
    op.create_index(
        'ix_page_fingerprints_site_id',
        'page_fingerprints', ['site_id'],
    )
    op.create_index(
        'ix_page_fingerprints_content_hash',
        'page_fingerprints', ['site_id', 'content_hash'],
    )
    op.create_index(
        'ix_page_fingerprints_last_fingerprinted_at',
        'page_fingerprints', ['last_fingerprinted_at'],
    )
    op.create_index(
        'ix_page_fingerprints_status_partial',
        'page_fingerprints', ['status'],
        postgresql_where=sa.text("status != 'fingerprinted'"),
    )


def downgrade() -> None:
    op.drop_index('ix_page_fingerprints_status_partial', table_name='page_fingerprints')
    op.drop_index('ix_page_fingerprints_last_fingerprinted_at', table_name='page_fingerprints')
    op.drop_index('ix_page_fingerprints_content_hash', table_name='page_fingerprints')
    op.drop_index('ix_page_fingerprints_site_id', table_name='page_fingerprints')
    op.drop_table('page_fingerprints')
