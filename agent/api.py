"""HTTP client functions for UnderCover backend operations."""

import os
from typing import Any, Literal

import httpx

BACKEND_URL = os.getenv("UNDERCOVER_BACKEND_URL", "http://127.0.0.1:8000")
API_TIMEOUT_SECONDS = float(os.getenv("UNDERCOVER_CLAIM_TIMEOUT_SECONDS", "120"))


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(
        base_url=BACKEND_URL, timeout=API_TIMEOUT_SECONDS, trust_env=False
    )


def get_claim(claim_id: str) -> dict[str, Any]:
    """Read the claim and supporting context through the backend."""
    with backend() as client:
        response = client.get(f"/api/agent/claims/{claim_id}")
    response.raise_for_status()
    return response.json()


def get_policy(claim_id: str) -> dict[str, Any]:
    """Read the policy attached to one claim."""
    return get_claim(claim_id)["policy"]


def get_claim_history(claim_id: str) -> list[dict[str, Any]]:
    """Read the customer's previous claims through the backend."""
    return get_claim(claim_id)["customer"]["previous_claims"]


def search_documents(
    claim_id: str,
    source: Literal["policy_wording", "claims_manual"],
    query: str,
) -> list[dict[str, Any]]:
    """Search one approved policy or manual source."""
    with backend() as client:
        response = client.post(
            "/api/agent/knowledge/search",
            json={"claim_id": claim_id, "source": source, "query": query},
        )
    response.raise_for_status()
    return response.json()["results"]


def read_section(
    claim_id: str,
    source: Literal["policy_wording", "claims_manual"],
    section_id: str,
) -> dict[str, Any]:
    """Read and record one source section."""
    with backend() as client:
        response = client.post(
            "/api/agent/knowledge/read",
            json={"claim_id": claim_id, "source": source, "section_id": section_id},
        )
    response.raise_for_status()
    return response.json()


def record_progress(claim_id: str, step: str, detail: str) -> None:
    """Persist one visible step in the claim review."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/progress",
            json={"step": step, "detail": detail},
        )
    response.raise_for_status()


def calculate_payout(
    claim_id: str,
    benefit: str,
    lines: list[dict[str, Any]],
    paid_by_others: float,
) -> dict[str, Any]:
    """Ask the backend to calculate the INR payout."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/payout",
            json={
                "benefit": benefit,
                "lines": lines,
                "paid_by_others": paid_by_others,
            },
        )
    response.raise_for_status()
    return response.json()


def record_decision(
    claim_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Persist a decision and let the backend calculate its payout."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/decision",
            json=request,
        )
    response.raise_for_status()
    return response.json()


def pay_claim(claim_id: str, amount_inr: float) -> dict[str, Any]:
    """Record one guarded simulated payment."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/payment", json={"amount_inr": amount_inr}
        )
    response.raise_for_status()
    return response.json()


def get_latest_run() -> dict[str, Any] | None:
    """Read the current or most recently completed queue run."""
    with backend() as client:
        response = client.get("/api/agent/runs/latest")
    response.raise_for_status()
    return response.json()["run"]


def get_agent_claim(claim_id: str) -> dict[str, Any]:
    """Read persisted agent workflow state for one claim."""
    with backend() as client:
        response = client.get(f"/api/agent/claims/{claim_id}")
    response.raise_for_status()
    return response.json()


def get_queue_claims() -> list[dict[str, Any]]:
    """Read the current queue."""
    with backend() as client:
        response = client.get("/api/claims")
    response.raise_for_status()
    return response.json()["claims"]


def get_instructions() -> dict[str, str]:
    """Read both instruction sets from the backend."""
    with backend() as client:
        response = client.get("/api/instructions")
    response.raise_for_status()
    return response.json()


def get_run(run_id: str) -> dict[str, Any]:
    """Read one persisted run and its ordered work items."""
    with backend() as client:
        response = client.get(f"/api/agent/runs/{run_id}")
    response.raise_for_status()
    return response.json()


def create_direct_run(max_claims: int) -> str:
    """Create a queue run that the current caller will process."""
    with backend() as client:
        response = client.post(
            "/api/agent/runs/direct", json={"max_claims": max_claims}
        )
    response.raise_for_status()
    return response.json()["run_id"]


def create_direct_claim_run(claim_id: str) -> str:
    """Create a named claim run for a local command."""
    with backend() as client:
        response = client.post(f"/api/claims/{claim_id}/run/direct")
    response.raise_for_status()
    return response.json()["run_id"]


def select_run_claims(run_id: str, claim_ids: list[str]) -> None:
    """Reserve claims in their selected order."""
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/select", json={"claim_ids": claim_ids}
        )
    response.raise_for_status()


def plan_run_todos(run_id: str, items: list[dict[str, str]]) -> None:
    """Persist the queue agent's ordered descriptions."""
    with backend() as client:
        response = client.put(f"/api/agent/runs/{run_id}/todos", json={"items": items})
    response.raise_for_status()


def update_run_todo(run_id: str, todo_id: int, status: str) -> None:
    """Persist a queue work item's state."""
    with backend() as client:
        response = client.patch(
            f"/api/agent/runs/{run_id}/todos/{todo_id}", json={"status": status}
        )
    response.raise_for_status()


def fail_run_attempt(run_id: str, claim_id: str, detail: str) -> dict[str, Any]:
    """Record a failed attempt and its retry state."""
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/claims/{claim_id}/failure",
            json={"detail": detail},
        )
    response.raise_for_status()
    return response.json()


def retry_run_claim(run_id: str, claim_id: str, reason: str) -> int:
    """Start another attempt for a selected claim."""
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/claims/{claim_id}/retry",
            json={"reason": reason},
        )
    response.raise_for_status()
    return int(response.json()["attempt"])


def complete_run(run_id: str, summary: str) -> None:
    """Save a run summary and close the run."""
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/complete", json={"summary": summary}
        )
    response.raise_for_status()


def interrupt_run(run_id: str) -> None:
    """Mark a stopped queue run as interrupted."""
    with backend() as client:
        response = client.post(f"/api/agent/runs/{run_id}/interrupt")
    response.raise_for_status()


def save_claim_thread(claim_id: str, thread_id: str) -> str:
    """Save the thread identifier for the active claim attempt."""
    with backend() as client:
        response = client.post(
            f"/api/agent/claims/{claim_id}/thread", json={"thread_id": thread_id}
        )
    response.raise_for_status()
    return response.json()["thread_id"]
