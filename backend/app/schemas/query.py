from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel


class QueryMetrics(BaseModel):
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    avg_position: float | None = None
    days_with_data: int = 0


class QueryChanges(BaseModel):
    impressions_pct: float | None = None
    clicks_pct: float | None = None
    position_delta: float | None = None  # positive = improved (moved up)


class QueryListItem(BaseModel):
    id: UUID
    query_text: str
    cluster: str | None = None
    is_branded: bool = False
    wordstat_volume: int | None = None
    current: QueryMetrics
    previous: QueryMetrics
    changes: QueryChanges
    first_seen_at: date | None = None
    last_seen_at: date | None = None


class QueryListResponse(BaseModel):
    total: int
    items: list[QueryListItem]


class QueryHistoryPoint(BaseModel):
    date: date
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    avg_position: float | None = None


class QueryDetailResponse(BaseModel):
    id: UUID
    query_text: str
    cluster: str | None = None
    is_branded: bool = False
    wordstat_volume: int | None = None
    history: list[QueryHistoryPoint]


class ClusterSummary(BaseModel):
    name: str
    query_count: int
    total_impressions: int = 0
    total_clicks: int = 0
    avg_position: float | None = None
    avg_ctr: float = 0.0
    top_queries: list[str] = []


class ClustersResponse(BaseModel):
    clusters: list[ClusterSummary]
    unclustered_count: int = 0
