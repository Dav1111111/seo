"""Locks helper — covers the basics of the advisory lock helper.

Pytest's default `db` fixture wraps each test in a rollback-only
transaction, so cross-session concurrency can't be faithfully exercised
here. The helper is thin — we verify it executes, is reentrant within
the same session, and returns the expected type from `try_lock`.
Semantics of `pg_advisory_xact_lock` itself are documented and tested
by Postgres.
"""

from __future__ import annotations

import uuid

from app.core_audit.sites.locks import (
    lock_site_target_config,
    try_lock_site_target_config,
)
from app.models.site import Site


async def test_lock_site_target_config_runs(db, test_site: Site):
    # Does not raise.
    await lock_site_target_config(db, test_site.id)


async def test_try_lock_returns_true_when_free(db):
    fresh_id = uuid.uuid4()  # unique per test run — nobody else holds it
    got = await try_lock_site_target_config(db, fresh_id)
    assert got is True


async def test_lock_accepts_uuid_and_str_forms(db):
    sid = uuid.uuid4()
    await lock_site_target_config(db, sid)
    await lock_site_target_config(db, str(sid))  # same lock, reentrant


async def test_lock_reentrant_within_session(db, test_site: Site):
    """Postgres advisory locks reference-count per session — acquiring
    twice must not deadlock."""
    await lock_site_target_config(db, test_site.id)
    await lock_site_target_config(db, test_site.id)
    got = await try_lock_site_target_config(db, test_site.id)
    assert got is True
