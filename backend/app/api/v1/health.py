import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.database import get_db
from app.config import settings
from app.health.connectors import CONNECTORS, CONNECTORS_BY_ID, describe_connector

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    db: str
    redis: str


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    db_ok = False
    redis_ok = False

    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    try:
        r = Redis.from_url(settings.REDIS_URL)
        await r.ping()
        redis_ok = True
        await r.aclose()
    except Exception:
        pass

    status = "ok" if db_ok and redis_ok else "degraded"
    return HealthResponse(
        status=status,
        db="connected" if db_ok else "error",
        redis="connected" if redis_ok else "error",
    )


# ── Connector status board ────────────────────────────────────────────────

@router.get("/health/connectors")
async def list_connectors() -> dict:
    """List all registered connectors with metadata (no live checks).

    Live status is fetched separately per-connector via
    `POST /health/connectors/{id}/test` so the listing stays fast and
    the UI can show "tap to check" rather than blocking on ~15 network
    calls.
    """
    return {
        "connectors": [describe_connector(c) for c in CONNECTORS],
        "count": len(CONNECTORS),
    }


@router.post("/health/connectors/{connector_id}/test")
async def run_connector_check(connector_id: str) -> dict:
    """Actually hit the external endpoint and return a fresh
    CheckResult. Sync checks run in a worker thread so the event loop
    stays free."""
    connector = CONNECTORS_BY_ID.get(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"unknown connector: {connector_id}")

    result = await anyio.to_thread.run_sync(connector.check)
    return {
        "id": connector.id,
        "name": connector.name,
        "category": connector.category,
        **result.to_dict(),
    }


@router.post("/health/connectors/test-all")
async def run_all_connector_checks() -> dict:
    """Run every check in parallel (via threads).

    Safe because each check is side-effect-free and reads only. Times
    out at the longest single-check budget (~10s).
    """
    results = {}

    async def _one(connector):
        r = await anyio.to_thread.run_sync(connector.check)
        results[connector.id] = {
            "id": connector.id,
            "name": connector.name,
            "category": connector.category,
            **r.to_dict(),
        }

    async with anyio.create_task_group() as tg:
        for c in CONNECTORS:
            tg.start_soon(_one, c)

    # Preserve registry order in response
    ordered = [results[c.id] for c in CONNECTORS if c.id in results]
    ok_count = sum(1 for r in ordered if r["ok"])
    return {
        "results": ordered,
        "total": len(ordered),
        "ok_count": ok_count,
        "failing": [r["id"] for r in ordered if not r["ok"]],
    }
