"""
Endpoints to trigger manual data collection and Yandex service discovery.
"""

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

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


@router.post("/sites/{site_id}/collect/metrica", response_model=CollectResponse)
async def trigger_metrica_collection(site_id: uuid.UUID):
    """Trigger manual Metrica data collection for a site."""
    from app.collectors.tasks import collect_site_metrica
    task = collect_site_metrica.delay(str(site_id))
    return CollectResponse(task_id=task.id, status="queued")


@router.post("/sites/{site_id}/cluster-queries", response_model=CollectResponse)
async def trigger_query_clustering(site_id: uuid.UUID):
    """Trigger AI query clustering for a site."""
    from app.agents.tasks import run_query_clustering_site
    task = run_query_clustering_site.delay(str(site_id), True)
    return CollectResponse(task_id=task.id, status="queued")


@router.post("/sites/{site_id}/pipeline", response_model=CollectResponse)
async def trigger_full_pipeline(site_id: uuid.UUID):
    """Run full issue pipeline: collect → detect → validate → store."""
    from app.agents.tasks import run_full_pipeline
    task = run_full_pipeline.delay(str(site_id), "manual")
    return CollectResponse(task_id=task.id, status="queued")


@router.get("/yandex/discover")
async def discover_yandex_services() -> dict[str, Any]:
    """Discover all Yandex Webmaster hosts and Metrica counters available to the configured token.

    Use this to find the correct host_id and counter_id values when setting up sites.
    """
    token = settings.YANDEX_OAUTH_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="YANDEX_OAUTH_TOKEN not configured")

    headers = {"Authorization": f"OAuth {token}", "Accept": "application/json"}
    result: dict[str, Any] = {"webmaster_hosts": [], "metrica_counters": [], "errors": []}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Webmaster — get user_id then hosts
        try:
            user_resp = await client.get(
                "https://api.webmaster.yandex.net/v4/user", headers=headers,
            )
            user_resp.raise_for_status()
            user_id = str(user_resp.json().get("user_id", ""))

            hosts_resp = await client.get(
                f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts",
                headers=headers,
            )
            hosts_resp.raise_for_status()
            for h in hosts_resp.json().get("hosts", []):
                result["webmaster_hosts"].append({
                    "host_id": h.get("host_id"),
                    "ascii_host_url": h.get("ascii_host_url"),
                    "unicode_host_url": h.get("unicode_host_url"),
                    "verified": h.get("verified", False),
                    "main_mirror": h.get("main_mirror"),
                })
            result["webmaster_user_id"] = user_id
        except Exception as exc:
            result["errors"].append(f"Webmaster: {exc}")

        # 2. Metrica — list counters
        try:
            counters_resp = await client.get(
                "https://api-metrika.yandex.net/management/v1/counters",
                headers=headers,
            )
            counters_resp.raise_for_status()
            for c in counters_resp.json().get("counters", []):
                result["metrica_counters"].append({
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "site": c.get("site"),
                    "status": c.get("status"),
                })
        except Exception as exc:
            result["errors"].append(f"Metrica: {exc}")

    return result
