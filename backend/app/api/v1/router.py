from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.sites import router as sites_router
from app.api.v1.collectors import router as collectors_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.chat import router as chat_router
from app.api.v1.queries import router as queries_router
from app.api.v1.tasks import router as tasks_router

v1_router = APIRouter()
v1_router.include_router(health_router, tags=["health"])
v1_router.include_router(sites_router, prefix="/sites", tags=["sites"])
v1_router.include_router(collectors_router, tags=["collectors"])
v1_router.include_router(dashboard_router, tags=["dashboard"])
v1_router.include_router(chat_router, tags=["chat"])
v1_router.include_router(queries_router, tags=["queries"])
v1_router.include_router(tasks_router, tags=["tasks"])
