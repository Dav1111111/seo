"""Per-query SERP snapshot — owner-roadmap point 2.

One row = «what we saw on Yandex for query X at time T» — top-N
domains/urls/titles/headlines, plus our own position if any.

Why history matters
-------------------
A weekly Celery beat probes the top-N most valuable queries and
INSERTS a new row every run. We never UPSERT-replace older rows — the
position trend (and «competitor X has been holding top-3 for six
weeks» narrative) needs the history. Both indexes cover «latest snapshot
per (site, query)» reads cheaply.

Field-name contract
-------------------
The JSONB `results` list is shaped {position, url, domain, title,
headline} — identical to ``serp_intel.SerpRanking`` so frontend can
parse either source with one mapper. Denormalised
``our_position``/``our_url``/``top_competitor_domains`` mirror the
same data but are faster to filter on than digging into JSONB.

Anti-fabrication contract
-------------------------
If the SERP fetch fails or returns empty, we still insert a row but
``error_tag`` is set and ``results=[]``. Callers MUST consult
``error_tag`` before treating the snapshot as a real observation —
an honest «no answer» beats fabricated zeros.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class QuerySerpSnapshot(Base):
    __tablename__ = "query_serp_snapshots"
    __table_args__ = (
        Index("ix_qss_site_query_taken", "site_id", "query_id", "taken_at"),
        Index("ix_qss_site_taken", "site_id", "taken_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_queries.id", ondelete="CASCADE"),
        nullable=False,
    )

    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Yandex region code. "225" = Russia country-wide (same default as
    # yandex_serp.DEFAULT_REGION). We store as text so future per-city
    # probes (Сочи = "239", Москва = "213") keep the same column.
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="225")

    # Top-N (typically 10) results as JSONB list of
    # {position, url, domain, title, headline}. Frozen schema — the
    # backend dto.SerpRanking dataclass mirrors this exactly so the
    # frontend can read either source with one parser.
    results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list,
    )

    # Convenience denormalised columns (faster filters than digging
    # into JSONB). `our_position` 1..N if our domain appears in top-N,
    # else NULL.  NULL is a meaningful state: «we asked, we are not in
    # top-N» — different from «we never asked» (no row at all).
    our_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    our_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # First 3 non-our domains in rank order. Brain rule and advisor
    # both read this without re-parsing the JSONB `results`.
    top_competitor_domains: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list,
    )

    # Honest no-answer marker. NULL → snapshot is a real observation.
    # Non-NULL → results=[] and top_competitor_domains=[]; callers MUST
    # treat this as «no signal» rather than «no competitors».
    error_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["QuerySerpSnapshot"]
