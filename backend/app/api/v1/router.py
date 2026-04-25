from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.sites import router as sites_router
from app.api.v1.collectors import router as collectors_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.intent import router as intent_router
from app.api.v1.review import router as review_router
from app.api.v1.priority import router as priority_router
from app.api.v1.report import router as report_router
from app.api.v1.admin_demand_map import router as admin_demand_map_router
from app.api.v1.activity import router as activity_router
from app.api.v1.admin_ops import router as admin_ops_router
from app.api.v1.business_truth import router as business_truth_router
from app.api.v1.playground import router as playground_router

# Core product loop — public product endpoints:
#   collectors → dashboard → intent → review → priority → report
# plus sites (profile management) and admin_demand_map (onboarding wizard).

v1_router = APIRouter()
v1_router.include_router(health_router, tags=["health"])
v1_router.include_router(sites_router, prefix="/sites", tags=["sites"])
v1_router.include_router(collectors_router, tags=["collectors"])
v1_router.include_router(dashboard_router, tags=["dashboard"])
v1_router.include_router(intent_router, tags=["intent"])
v1_router.include_router(review_router, tags=["review"])
v1_router.include_router(priority_router, tags=["priority"])
v1_router.include_router(report_router, tags=["report"])
v1_router.include_router(admin_demand_map_router, tags=["admin-demand-map"])
v1_router.include_router(activity_router, tags=["activity"])
v1_router.include_router(admin_ops_router, tags=["admin-ops"])
v1_router.include_router(business_truth_router, tags=["business-truth"])
v1_router.include_router(playground_router, tags=["playground"])
