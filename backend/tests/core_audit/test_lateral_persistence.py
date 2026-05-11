"""Lateral persistence: owner-decision protection invariant.

The persistence helper MUST NOT overwrite a row whose status the
owner already set to accepted/rejected/promoted, even if the LLM
re-proposes the same query with different confidence. This test
pins that contract — if it ever regresses, owners' triage decisions
silently disappear on the next weekly run.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.lateral.dto import LateralCandidate
from app.core_audit.lateral.persistence import (
    set_status,
    upsert_lateral_candidates,
)
from app.models.lateral_query import LateralQuery


@pytest.mark.asyncio
async def test_first_run_inserts_all(db: AsyncSession, test_site) -> None:
    cands = [
        LateralCandidate(
            query="однодневные туры из адлера",
            relation="related",
            confidence=0.7,
            rationale="Тот же покупатель ищет короткие выезды.",
        ),
        LateralCandidate(
            query="что посмотреть в гаграх",
            relation="info",
            confidence=0.5,
            rationale="Информационный спрос рядом с продуктом.",
        ),
    ]
    stats = await upsert_lateral_candidates(
        db, test_site.id, cands, agent_run_id=None,
    )
    assert stats == {
        "inserted": 2, "refreshed": 0, "skipped_owner_locked": 0,
    }


@pytest.mark.asyncio
async def test_second_run_refreshes_only_new(
    db: AsyncSession, test_site,
) -> None:
    """LLM re-proposes the same query → confidence/rationale refresh."""
    first = [
        LateralCandidate(
            query="экскурсии в красную поляну",
            relation="related",
            confidence=0.4,
            rationale="Первая итерация: слабая уверенность.",
        ),
    ]
    await upsert_lateral_candidates(db, test_site.id, first, None)

    second = [
        LateralCandidate(
            query="экскурсии в красную поляну",
            relation="direct",
            confidence=0.85,
            rationale="LLM передумала: сильный сигнал.",
        ),
    ]
    stats = await upsert_lateral_candidates(db, test_site.id, second, None)
    assert stats["inserted"] == 0
    assert stats["refreshed"] == 1

    row = (await db.execute(select(LateralQuery))).scalar_one()
    assert row.relation == "direct"
    assert float(row.confidence) == pytest.approx(0.85)
    assert row.rationale.startswith("LLM передумала")


@pytest.mark.asyncio
async def test_owner_rejected_row_is_immutable(
    db: AsyncSession, test_site,
) -> None:
    """The invariant. Owner sets status=rejected → LLM cannot revive it."""
    initial = [
        LateralCandidate(
            query="джип тур в абхазию",
            relation="related",
            confidence=0.6,
            rationale="Первое предложение.",
        ),
    ]
    await upsert_lateral_candidates(db, test_site.id, initial, None)

    row = (await db.execute(select(LateralQuery))).scalar_one()
    changed = await set_status(db, test_site.id, row.id, "rejected")
    assert changed is True

    proposed_again = [
        LateralCandidate(
            query="джип тур в абхазию",
            relation="direct",
            confidence=0.99,
            rationale="LLM думает, что это идеально.",
        ),
    ]
    stats = await upsert_lateral_candidates(
        db, test_site.id, proposed_again, agent_run_id=None,
    )
    assert stats == {
        "inserted": 0, "refreshed": 0, "skipped_owner_locked": 1,
    }

    row_after = (await db.execute(select(LateralQuery))).scalar_one()
    assert row_after.status == "rejected"
    assert row_after.relation == "related"
    assert float(row_after.confidence) == pytest.approx(0.6)
    assert row_after.rationale == "Первое предложение."


@pytest.mark.asyncio
async def test_status_scoping_blocks_cross_site_mutation(
    db: AsyncSession, test_tenant,
) -> None:
    """A leaked lateral_id from site A must not let site B change it."""
    from app.models.site import Site

    site_a = Site(
        tenant_id=test_tenant.id,
        domain=f"a-{uuid.uuid4().hex[:8]}.example",
        operating_mode="recommend",
        is_active=True,
        onboarding_step="active",
        target_config={"services": ["t"], "geo_primary": ["t"]},
    )
    site_b = Site(
        tenant_id=test_tenant.id,
        domain=f"b-{uuid.uuid4().hex[:8]}.example",
        operating_mode="recommend",
        is_active=True,
        onboarding_step="active",
        target_config={"services": ["t"], "geo_primary": ["t"]},
    )
    db.add_all([site_a, site_b])
    await db.flush()

    await upsert_lateral_candidates(
        db, site_a.id,
        [LateralCandidate(
            query="прогулка на катере", relation="related",
            confidence=0.5, rationale=".",
        )],
        None,
    )
    row_a = (
        await db.execute(
            select(LateralQuery).where(LateralQuery.site_id == site_a.id)
        )
    ).scalar_one()

    # Try to mutate site_a's row through site_b's scope.
    changed = await set_status(db, site_b.id, row_a.id, "accepted")
    assert changed is False

    refreshed = (await db.execute(select(LateralQuery))).scalar_one()
    assert refreshed.status == "new"


@pytest.mark.asyncio
async def test_set_status_rejects_unknown_status(
    db: AsyncSession, test_site,
) -> None:
    with pytest.raises(ValueError):
        await set_status(db, test_site.id, uuid.uuid4(), "deprecated")
