import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.deps import require_admin
from app.config import settings
from app.database import get_db
from app.models.site import Site
from app.models.tenant import Tenant
from app.schemas.site import SiteCreate, SiteUpdate, SiteResponse
from app.security.crypto import EncryptionKeyMissing, encrypt_secret

router = APIRouter()
admin_router = APIRouter(prefix="/admin")

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


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Thin wrapper preserved for in-module Depends() callers.

    All policy lives in :func:`app.api.v1.deps.require_admin` which uses
    :func:`secrets.compare_digest` to avoid timing leaks.
    """
    require_admin(x_admin_key or "")


async def _create_site(body: SiteCreate, db: AsyncSession) -> Site:
    tenant_id = await _ensure_default_tenant(db)
    payload = body.model_dump(exclude_none=True)
    try:
        if "yandex_oauth_token" in payload:
            payload["yandex_oauth_token"] = encrypt_secret(payload["yandex_oauth_token"])
    except EncryptionKeyMissing as exc:
        raise HTTPException(status_code=500, detail="ENCRYPTION_KEY not configured") from exc
    site = Site(tenant_id=tenant_id, **payload)
    db.add(site)
    await db.flush()
    await db.refresh(site)
    return site


async def _update_site(site_id: uuid.UUID, body: SiteUpdate, db: AsyncSession) -> Site:
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    payload = body.model_dump(exclude_none=True)
    try:
        if "yandex_oauth_token" in payload:
            payload["yandex_oauth_token"] = encrypt_secret(payload["yandex_oauth_token"])
    except EncryptionKeyMissing as exc:
        raise HTTPException(status_code=500, detail="ENCRYPTION_KEY not configured") from exc
    for key, value in payload.items():
        setattr(site, key, value)
    await db.flush()
    await db.refresh(site)
    return site


@router.get("", response_model=list[SiteResponse])
async def list_sites(db: AsyncSession = Depends(get_db)):
    tenant_id = await _ensure_default_tenant(db)
    result = await db.execute(select(Site).where(Site.tenant_id == tenant_id).order_by(Site.created_at))
    return result.scalars().all()


@router.post("", response_model=SiteResponse, status_code=201)
async def create_site(
    body: SiteCreate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _create_site(body, db)


@admin_router.post("/sites", response_model=SiteResponse, status_code=201)
async def admin_create_site(
    body: SiteCreate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _create_site(body, db)


@router.get("/{site_id}", response_model=SiteResponse)
async def get_site(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.patch("/{site_id}", response_model=SiteResponse)
async def update_site(
    site_id: uuid.UUID,
    body: SiteUpdate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _update_site(site_id, body, db)


@admin_router.patch("/sites/{site_id}", response_model=SiteResponse)
async def admin_update_site(
    site_id: uuid.UUID,
    body: SiteUpdate,
    _: None = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _update_site(site_id, body, db)
