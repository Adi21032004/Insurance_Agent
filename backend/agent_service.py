"""HTTP calls from the backend to the UnderCover Agent service."""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AgentServiceConflict(RuntimeError):
    """Raised when the agent service refuses overlapping work."""


def agent_url() -> str:
    """Return the configured agent service address."""
    return os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")


def post_agent(path: str, json_body: dict[str, Any] | None = None) -> httpx.Response:
    """Post to the local service without environment proxy settings."""
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.post(f"{agent_url()}{path}", json=json_body)
        response.raise_for_status()
        return response


def run_queue(run_id: str) -> None:
    """Ask the agent service to process one saved queue run."""
    try:
        post_agent(f"/api/runs/{run_id}/run")
    except httpx.HTTPError as error:
        logger.warning("Could not start agent run %s: %s", run_id, error)


def resume_claim(claim_id: str) -> None:
    """Ask the agent service to carry on a signed-off claim."""
    try:
        post_agent(f"/api/claims/{claim_id}/resume")
    except httpx.HTTPError as error:
        logger.warning("Could not resume signed-off claim %s: %s", claim_id, error)


def start_dispatch(agents: int) -> dict[str, Any]:
    """Ask the agent service to start a dispatch with N claim agents at once."""
    try:
        response = post_agent("/api/dispatches", {"agents": agents})
        return response.json()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == httpx.codes.CONFLICT:
            detail = error.response.json().get("detail", "A run is already active")
            raise AgentServiceConflict(str(detail)) from error
        raise RuntimeError("The agent's service isn't running") from error
    except httpx.HTTPError as error:
        raise RuntimeError("The agent's service isn't running") from error


def start_evaluation(evaluation_set: str, tiny: bool, agents: int) -> dict[str, Any]:
    """Ask the agent service to start an evaluation."""
    try:
        response = post_agent(
            "/api/evaluations",
            {"evaluation_set": evaluation_set, "tiny": tiny, "agents": agents},
        )
        return response.json()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == httpx.codes.CONFLICT:
            detail = error.response.json().get("detail", "A run is already active")
            raise AgentServiceConflict(str(detail)) from error
        raise RuntimeError("The agent's service isn't running") from error
    except httpx.HTTPError as error:
        raise RuntimeError("The agent's service isn't running") from error
