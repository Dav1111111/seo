"""
Endpoints to trigger manual data collection.
"""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class CollectResponse(BaseModel):
    task_id: str
    status: str


@router.post("/sites/{site_id}/collect/webmaster", response_model=CollectResponse)
async def trigger_webmaster_collection(site_id: uuid.UUID):
    """Trigger manual Webmaster data collection for a site."""
    from app.collectors.tasks import collect_site_webmaster
    task = collect_site_webmaster.delay(str(site_id))
    return CollectResponse(task_id=task.id, status="queued")


@router.post("/collect/all", response_model=CollectResponse)
async def trigger_collect_all():
    """Trigger collection for all active sites."""
    from app.collectors.tasks import collect_webmaster_all
    task = collect_webmaster_all.delay()
    return CollectResponse(task_id=task.id, status="queued")


@router.post("/sites/{site_id}/analyse/{agent_name}", response_model=CollectResponse)
async def trigger_agent(site_id: uuid.UUID, agent_name: str):
    """Trigger a specific AI agent for a site."""
    valid = {"search_visibility", "technical_indexing"}
    if agent_name not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown agent. Valid: {valid}")
    from app.agents.tasks import run_agent_for_site
    task = run_agent_for_site.delay(agent_name, str(site_id), "manual")
    return CollectResponse(task_id=task.id, status="queued")


@router.post("/sites/{site_id}/analyse/all", response_model=CollectResponse)
async def trigger_all_agents(site_id: uuid.UUID):
    """Run all agents for a site (collect + analyse)."""
    from app.collectors.tasks import collect_site_webmaster
    from app.agents.tasks import run_agent_for_site
    # Trigger in sequence via Celery
    collect_site_webmaster.delay(str(site_id))
    t1 = run_agent_for_site.apply_async(
        args=["search_visibility", str(site_id), "manual"], countdown=5
    )
    run_agent_for_site.apply_async(
        args=["technical_indexing", str(site_id), "manual"], countdown=6
    )
    return CollectResponse(task_id=t1.id, status="queued")


@router.post("/sites/{site_id}/pipeline", response_model=CollectResponse)
async def trigger_full_pipeline(site_id: uuid.UUID):
    """Run full issue pipeline: collect → detect → validate → store."""
    from app.agents.tasks import run_full_pipeline
    task = run_full_pipeline.delay(str(site_id), "manual")
    return CollectResponse(task_id=task.id, status="queued")
