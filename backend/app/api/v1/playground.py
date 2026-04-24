"""HTTP surface for the API Playground.

Stateless by design: each `/run` call takes the scenario id, the step
index to run, the user inputs, and the prior steps' outputs. Server
never stores a session — the frontend owns continuation state.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.playground.scenarios import SCENARIO_META, list_scenarios, run_step


router = APIRouter(prefix="/playground")


@router.get("/scenarios")
async def get_scenarios() -> dict:
    """List all playground scenarios with their input fields."""
    return {"scenarios": list_scenarios()}


class RunStepBody(BaseModel):
    scenario_id: str
    step_index: int = Field(ge=0, le=19)
    inputs: dict = Field(default_factory=dict)
    # Previous steps' output payloads (`response_summary` dicts from prior
    # StepResult.to_dict() calls). The frontend appends to this list as
    # it goes. Server reads what it needs, ignores the rest.
    prior: list[dict] = Field(default_factory=list)


@router.post("/run")
async def run_scenario_step(body: RunStepBody) -> dict:
    """Execute exactly one step of a scenario and return its payload."""
    if body.scenario_id not in SCENARIO_META:
        raise HTTPException(status_code=404, detail=f"unknown scenario: {body.scenario_id}")

    # Sync scenario body → thread so the event loop stays free. Every
    # step inside is short (≤10 s), so no need for a full Celery path.
    result = await anyio.to_thread.run_sync(
        run_step, body.scenario_id, body.step_index, body.inputs, body.prior,
    )
    return result.to_dict()
