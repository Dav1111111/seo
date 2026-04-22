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


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """An AsyncSession bound to a transaction that's rolled back on exit.

    The engine is created per-test, not session-scoped: asyncpg
    connections hold a reference to the event loop they were created
    in, and pytest-asyncio uses a fresh loop per test by default — so
    a session-scoped engine produces "attached to a different loop"
    errors on the second test.

    Use this in any test that touches tables. Nothing persists past
    the test.
    """
    eng = create_async_engine(DATABASE_URL, echo=False)
    conn = await eng.connect()
    trans = await conn.begin()
    maker = async_sessionmaker(bind=conn, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await eng.dispose()


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
