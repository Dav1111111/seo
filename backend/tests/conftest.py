"""Shared test fixtures.

Every test that touches the DB runs inside its own transaction that
gets rolled back on teardown — so the database stays clean between
tests and we never need separate test schemas.

Canonical invocation:
    docker compose exec backend pytest tests -q
"""

from __future__ import annotations

import os
import uuid
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.models.site import Site
from app.models.tenant import Tenant


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://tower:devpassword@db/growthtower",
)


@pytest_asyncio.fixture(scope="session")
async def engine():
    """One AsyncEngine for the test session."""
    eng = create_async_engine(DATABASE_URL, echo=False)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """An AsyncSession bound to a transaction that's rolled back on exit.

    Use this in any test that touches tables — inserts, events, updates
    to sites, whatever. Nothing persists past the test.
    """
    conn = await engine.connect()
    trans = await conn.begin()
    maker = async_sessionmaker(bind=conn, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def test_tenant(db: AsyncSession) -> Tenant:
    """A disposable tenant for the test (rolled back)."""
    t = Tenant(name="test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.flush()
    return t


@pytest_asyncio.fixture
async def test_site(db: AsyncSession, test_tenant: Tenant) -> Site:
    """A minimal active site ready for tasks/API tests."""
    s = Site(
        tenant_id=test_tenant.id,
        domain=f"test-{uuid.uuid4().hex[:8]}.example",
        operating_mode="recommend",
        is_active=True,
        onboarding_step="active",
        target_config={"services": ["test"], "geo_primary": ["test"]},
    )
    db.add(s)
    await db.flush()
    return s
