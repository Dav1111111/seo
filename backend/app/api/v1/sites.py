import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.site import Site
from app.models.tenant import Tenant
from app.schemas.site import SiteCreate, SiteUpdate, SiteResponse

router = APIRouter()

# Temporary: hardcoded tenant for Phase 1 (replaced by auth in Phase 10)
DEFAULT_TENANT_SLUG = "default"


async def _ensure_default_tenant(db: AsyncSession) -> uuid.UUID:
    result = await db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG))
    tenant = result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name="Default", slug=DEFAULT_TENANT_SLUG)
        db.add(tenant)
        await db.flush()
    return tenant.id


@router.get("", response_model=list[SiteResponse])
async def list_sites(db: AsyncSession = Depends(get_db)):
    tenant_id = await _ensure_default_tenant(db)
    result = await db.execute(select(Site).where(Site.tenant_id == tenant_id).order_by(Site.created_at))
    return result.scalars().all()


@router.post("", response_model=SiteResponse, status_code=201)
async def create_site(body: SiteCreate, db: AsyncSession = Depends(get_db)):
    tenant_id = await _ensure_default_tenant(db)
    site = Site(tenant_id=tenant_id, **body.model_dump(exclude_none=True))
    db.add(site)
    await db.flush()
    await db.refresh(site)
    return site


@router.get("/{site_id}", response_model=SiteResponse)
async def get_site(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.patch("/{site_id}", response_model=SiteResponse)
async def update_site(site_id: uuid.UUID, body: SiteUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(site, key, value)
    await db.flush()
    await db.refresh(site)
    return site
