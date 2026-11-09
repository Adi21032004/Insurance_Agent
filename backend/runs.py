from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import agent_service, database

MAX_AGENTS = 25
router = APIRouter()


class DispatchStartInput(BaseModel):
    agents: int = Field(ge=1, le=25)


class EvaluationStartInput(BaseModel):
    evaluation_set: str = Field(min_length=1)
    tiny: bool
    agents: int = Field(ge=1, le=25)


class RunClaimInput(BaseModel):
    claim_ids: list[str]


class FinishRunInput(BaseModel):
    summary: str = Field(min_length=1)


@router.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(body: DispatchStartInput) -> dict[str, Any]:
    """Trigger a dispatch through the agent service and return its accepted status."""
    try:
        agent_service.start_dispatch(body.agents)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"status": "accepted", "agents": body.agents}


@router.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(body: EvaluationStartInput) -> dict[str, Any]:
    """Accept an evaluation request and send it to the agent service."""
    try:
        agent_service.start_evaluation(
            body.evaluation_set,
            body.tiny,
            body.agents,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "status": "accepted",
        "evaluation_set": body.evaluation_set,
        "tiny": body.tiny,
    }


@router.get("/api/dispatches/latest", operation_id="get_latest_dispatch")
def get_latest_dispatch() -> dict[str, Any] | None:
    """Return the newest dispatch, if it exists."""
    return database.get_latest_dispatch()


@router.get("/api/evaluations", operation_id="list_evaluations")
def list_evaluations() -> list[dict[str, Any]]:
    """Return evaluations newest first."""
    return database.list_evaluations()


@router.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(run_id: str) -> dict[str, Any]:
    """Return one evaluation with its measures and claims."""
    evaluation = database.get_evaluation(run_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return evaluation


@router.post("/api/runs/dispatches", operation_id="create_dispatch")
def create_dispatch(body: DispatchStartInput) -> dict[str, Any]:
    """Persist a dispatch record and return it."""
    if not 1 <= body.agents <= MAX_AGENTS:
        raise HTTPException(status_code=422, detail="Dispatch agents must be 1-25")
    run_id = f"dispatch-{len(database.list_evaluations()) + 1}"
    return database.create_dispatch(
        run_id,
        body.agents,
        "openai/gpt-6-luna",
        "low",
    )


@router.post("/api/runs/evaluations", operation_id="create_evaluation")
def create_evaluation(body: EvaluationStartInput) -> dict[str, Any]:
    """Persist an evaluation record and return it."""
    if not 1 <= body.agents <= MAX_AGENTS:
        raise HTTPException(status_code=422, detail="Evaluation agents must be 1-25")
    run_id = f"eval-{len(database.list_evaluations()) + 1}"
    return database.create_evaluation(
        run_id,
        body.evaluation_set,
        body.tiny,
        body.agents,
        "openai/gpt-6-luna",
        "low",
    )


@router.put("/api/runs/{run_id}/claims", operation_id="record_run_claims")
def record_run_claims(run_id: str, body: RunClaimInput) -> dict[str, Any]:
    """Persist a run's claims in order."""
    database.record_run_claims(run_id, body.claim_ids)
    return {"run_id": run_id, "claim_ids": body.claim_ids}


@router.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: str, body: FinishRunInput) -> dict[str, Any]:
    """Finish a run and save its summary."""
    database.finish_run_summary(run_id, body.summary)
    return {"run_id": run_id, "status": "finished", "summary": body.summary}


@router.post("/api/runs/stop", operation_id="stop_runs")
def stop_runs() -> dict[str, Any]:
    """Stop all currently active runs."""
    return {"run_ids": database.stop_runs()}
