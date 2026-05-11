"""page_deep_extracts: full Playwright-rendered snapshot of a URL.

Stores the deep extraction of one URL — both for own pages and for
competitor URLs (`page_id` is nullable: own pages link to it, competitor
extracts only have site_id). Designed to be re-runnable: every extract
is a new row, latest-by-extracted_at wins for the chat context.

Why JSONB everywhere: the extraction shape evolves quickly (new CSS
metrics, new performance fields, new DOM facts). JSONB lets us add
fields without alembic migrations every week — production data is
not the place to lock in a schema.

Revision ID: e8f9c1a2b4d6
Revises: 1a3c5e7b9d02
Create Date: 2026-05-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e8f9c1a2b4d6"
down_revision: Union[str, None] = "1a3c5e7b9d02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_deep_extracts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pages.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="NULL for competitor URLs — they're not in our pages table",
        ),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column(
            "is_competitor",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "competitor_domain",
            sa.String(255),
            nullable=True,
            comment="Set when is_competitor=true; for grouping in UI",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="completed",
            comment="completed | failed | timeout",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "duration_ms",
            sa.Integer,
            nullable=True,
            comment="Wall-clock time of the Playwright run",
        ),
        # ── Content ──────────────────────────────────────────────────
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("h1", sa.Text, nullable=True),
        sa.Column("meta_description", sa.Text, nullable=True),
        sa.Column(
            "full_text",
            sa.Text,
            nullable=True,
            comment="Visible text after JS render, capped to ~50KB",
        ),
        sa.Column(
            "headings_tree",
            postgresql.JSONB,
            nullable=True,
            comment="Ordered list of {level, text} for H1-H4",
        ),
        # ── Interactive elements (the heart of UX analysis) ──────────
        sa.Column(
            "cta_inventory",
            postgresql.JSONB,
            nullable=True,
            comment="Buttons + links with text, color, position, size, "
                    "above_fold flag",
        ),
        sa.Column(
            "forms_inventory",
            postgresql.JSONB,
            nullable=True,
            comment="Forms with field count, types, position",
        ),
        sa.Column(
            "links_inventory",
            postgresql.JSONB,
            nullable=True,
            comment="Internal/external links with anchor + href",
        ),
        sa.Column(
            "images_inventory",
            postgresql.JSONB,
            nullable=True,
            comment="Images with alt, src, dimensions, lazy-loading",
        ),
        # ── Visual signals ───────────────────────────────────────────
        sa.Column(
            "css_palette",
            postgresql.JSONB,
            nullable=True,
            comment="Top-N hex colors with usage count",
        ),
        sa.Column(
            "fonts",
            postgresql.JSONB,
            nullable=True,
            comment="Font families + sizes used on page",
        ),
        sa.Column(
            "layout_meta",
            postgresql.JSONB,
            nullable=True,
            comment="Viewport size, fold position, document height, "
                    "whether page has sticky header/CTA",
        ),
        # ── Performance ──────────────────────────────────────────────
        sa.Column(
            "performance",
            postgresql.JSONB,
            nullable=True,
            comment="LCP, FCP, CLS, TBT, total load time, JS errors count",
        ),
        sa.Column(
            "js_errors",
            postgresql.JSONB,
            nullable=True,
            comment="List of console errors observed during render",
        ),
        # ── Schema.org ───────────────────────────────────────────────
        sa.Column(
            "schema_blocks",
            postgresql.JSONB,
            nullable=True,
            comment="Parsed JSON-LD blocks found on page",
        ),
        # ── Screenshots (URLs to local storage / S3-like) ────────────
        sa.Column(
            "screenshot_desktop_path",
            sa.String(500),
            nullable=True,
        ),
        sa.Column(
            "screenshot_mobile_path",
            sa.String(500),
            nullable=True,
        ),
        # ── Owner-friendly summary (LLM-rendered, optional) ──────────
        sa.Column(
            "ai_summary_md",
            sa.Text,
            nullable=True,
            comment="Optional pre-rendered Markdown summary by LLM",
        ),
    )

    op.create_index(
        "ix_page_deep_extracts_site_extracted",
        "page_deep_extracts",
        ["site_id", sa.text("extracted_at DESC")],
    )
    op.create_index(
        "ix_page_deep_extracts_competitor",
        "page_deep_extracts",
        ["site_id", "competitor_domain"],
        postgresql_where=sa.text("is_competitor = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_page_deep_extracts_competitor", table_name="page_deep_extracts")
    op.drop_index("ix_page_deep_extracts_site_extracted", table_name="page_deep_extracts")
    op.drop_table("page_deep_extracts")
