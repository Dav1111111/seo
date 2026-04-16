"""Shared async DB session helper for Celery tasks.

Each Celery task invocation spins up its own AsyncEngine (asyncpg cannot share
connections between event loops/tasks safely), and MUST dispose it after use
to release pool connections back to Postgres. Forgetting `await eng.dispose()`
leaks connections — with worker_max_tasks_per_child=50 and pool_size=2, up to
100 connections per worker cycle, exhausting `max_connections` quickly.

Usage
-----
    from app.workers.db_session import task_session

    async def _inner():
        async with task_session() as db:
            return await MyService().do(db, ...)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """Yield a fresh AsyncSession backed by a dedicated engine.

    The engine is disposed (pool drained, connections closed) when the
    context exits — even on exceptions. One engine per task invocation.
    """
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    try:
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await eng.dispose()


@asynccontextmanager
async def task_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory for tasks that need multiple independent sessions.

    Useful when a task opens several short sessions sequentially (e.g. bulk
    fingerprinting with a semaphore). The underlying engine is disposed when
    the context exits.
    """
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()
