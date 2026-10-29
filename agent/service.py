"""HTTP service for claim continuations and persisted queue runs."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent import api
from agent.harness import (
    build_claim_graph,
    carry_on,
    checkpointer,
    model,
    process_saved_run,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Keep one checkpoint pool open for the life of this process."""
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


def run_in_background(run_id: str, saver: Any) -> None:
    """Run a persisted queue in the service process."""
    try:
        logger.info(process_saved_run(run_id, saver))
    except Exception:
        logger.exception("Agent run %s did not finish", run_id)


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent service is running."""
    return {"status": "ok"}


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
