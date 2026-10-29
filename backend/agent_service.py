"""HTTP calls from the backend to the UnderCover Agent service."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def agent_url() -> str:
    """Return the configured agent service address."""
    return os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")


def post_agent(path: str) -> None:
    """Post to the local service without environment proxy settings."""
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.post(f"{agent_url()}{path}")
        response.raise_for_status()


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
