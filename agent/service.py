"""HTTP service for claim continuations and persisted queue runs."""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import api, dispatcher
from agent.harness import (
    build_claim_graph,
    carry_on,
    checkpointer,
    model,
    process_saved_run,
    work_in_run,
)

logger = logging.getLogger(__name__)


class DispatchRequest(BaseModel):
    agents: int = Field(ge=1, le=25)


class EvaluationRequest(BaseModel):
    evaluation_set: str = Field(min_length=1)
    tiny: bool
    agents: int = Field(ge=1, le=25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Keep one checkpoint pool open for the life of this process."""
    app.state.run_lock = threading.Lock()
    app.state.resuming = set()
    with checkpointer() as saver:
        app.state.checkpointer = saver
        yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


def resume_in_background(claim_id: str, saver: Any) -> None:
    """Carry a signed-off claim on without holding the handler response."""
    try:
        api.get_agent_claim(claim_id)
        graph = build_claim_graph(model(claim_id), saver)
        logger.info(carry_on(claim_id, graph))
    except Exception:
        logger.exception("Could not resume signed-off claim %s", claim_id)
    finally:
        app.state.resuming.discard(claim_id)


def run_in_background(run_id: str, saver: Any) -> None:
    """Run a persisted queue in the service process."""
    try:
        logger.info(process_saved_run(run_id, saver))
    except Exception:
        logger.exception("Agent run %s did not finish", run_id)


def dispatch_in_background(agents: int, saver: Any) -> None:
    """Create and execute one dispatch with bounded parallel claim workers."""
    run_id: str | None = None
    try:
        model_name = os.getenv("MODEL_NAME") or "openai/gpt-6-luna"
        reasoning_effort = "none" if os.getenv("MODEL_NAME") else "low"
        run = api.create_dispatch(str(uuid4()), agents, model_name, reasoning_effort)
        run_id = run["run_id"]
        if not isinstance(run_id, str):
            raise RuntimeError("The backend did not return a dispatch id")
        claim_ids = [
            claim["claim_id"]
            for claim in api.get_dispatch_claims()
            if claim["status"] in {"waiting", "in progress"}
        ]
        api.record_run_claims(run_id, claim_ids)
        run.update({"agents": agents, "claim_ids": claim_ids})
        resuming = set(app.state.resuming)
        reports = dispatcher.work_run(
            run,
            lambda claim_id, active_run_id: work_in_run(
                claim_id,
                active_run_id,
                lambda: build_claim_graph(model(claim_id), saver),
            ),
            resuming,
        )
        logger.info("Dispatch %s finished: %s", run_id, reports)
    except Exception:
        logger.exception("Dispatch failed")
        if run_id is not None:
            try:
                api.stop_runs()
            except Exception:
                logger.exception("Could not stop failed dispatch %s", run_id)
    finally:
        app.state.run_lock.release()


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent service is running."""
    return {"status": "ok"}


@app.post("/api/dispatches", status_code=202, operation_id="start_dispatch")
def start_dispatch(
    request: DispatchRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Accept a dispatch request while a single run is active."""
    if not app.state.run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="A dispatch or an evaluation is already running"
        )
    background_tasks.add_task(
        dispatch_in_background, request.agents, app.state.checkpointer
    )
    return {"status": "accepted", "agents": request.agents}


@app.post("/api/evaluations", status_code=202, operation_id="start_evaluation")
def start_evaluation(
    request: EvaluationRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Accept an evaluation request while a single run is active."""
    if not app.state.run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="A dispatch or an evaluation is already running"
        )
    app.state.run_lock.release()
    raise HTTPException(
        status_code=501, detail="Evaluation dispatch is not implemented yet"
    )
    return {
        "status": "accepted",
        "evaluation_set": request.evaluation_set,
        "tiny": request.tiny,
        "agents": request.agents,
    }


@app.post(
    "/api/claims/{claim_id}/resume",
    status_code=202,
    operation_id="resume_claim",
)
def resume_claim(claim_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Pick a signed-off claim up from its saved pause."""
    try:
        record = api.get_agent_claim(claim_id)
    except Exception as error:
        raise HTTPException(status_code=404, detail="Claim not found") from error
    if record["status"] != "in progress":
        raise HTTPException(status_code=409, detail="Claim is not in progress")
    app.state.resuming.add(claim_id)
    background_tasks.add_task(resume_in_background, claim_id, app.state.checkpointer)
    return {"claim_id": claim_id, "status": "accepted"}


@app.post("/api/runs/{run_id}/run", status_code=202, operation_id="run_saved_queue")
def run_saved_queue(run_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Start one persisted queue run in the agent service."""
    try:
        run = api.get_run(run_id)
    except Exception as error:
        raise HTTPException(status_code=404, detail="Run not found") from error
    if run["status"] != "in progress":
        raise HTTPException(status_code=409, detail="Run is not in progress")
    background_tasks.add_task(run_in_background, run_id, app.state.checkpointer)
    return {"run_id": run_id, "status": "accepted"}
