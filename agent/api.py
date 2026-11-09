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


def record_model_call(
    claim_id: str,
    run_id: str | None,
    input_tokens: int,
    output_tokens: int,
    cost: float,
) -> None:
    """Persist token usage and cost for one model response."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/model-calls",
            json={
                "run_id": run_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
            },
        )
    response.raise_for_status()


def record_work_time(claim_id: str, run_id: str | None, seconds: float) -> None:
    """Persist the duration of one claim-agent invocation."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/work-times",
            json={"run_id": run_id, "seconds": seconds},
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


def get_agent_claim(claim_id: str) -> dict[str, Any]:
    """Read persisted agent workflow state for one claim."""
    with backend() as client:
        response = client.get(f"/api/agent/claims/{claim_id}")
    response.raise_for_status()
    return response.json()


def get_dispatch_claims() -> list[dict[str, Any]]:
    """Read claims eligible for a dispatch from the handler queue."""
    with backend() as client:
        response = client.get("/api/claims")
    response.raise_for_status()
    return [
        claim
        for claim in response.json()["claims"]
        if claim["status"] in {"waiting", "in progress"}
    ]


def get_instructions() -> dict[str, str]:
    """Read the claim instructions from the backend."""
    with backend() as client:
        response = client.get("/api/instructions")
    response.raise_for_status()
    return response.json()


def create_dispatch(
    run_id: str, agents: int, model: str, reasoning_effort: str
) -> dict[str, Any]:
    """Create one dispatch record through the backend."""
    with backend() as client:
        response = client.post(
            "/api/runs/dispatches",
            json={
                "run_id": run_id,
                "agents": agents,
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
        )
    response.raise_for_status()
    return response.json()


def create_evaluation(  # noqa: PLR0913, PLR0917
    run_id: str,
    evaluation_set: Literal["evaluation", "held_out"],
    tiny: bool,
    agents: int,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Create an evaluation with its labelled claim copies through the backend."""
    with backend() as client:
        response = client.post(
            "/api/runs/evaluations",
            json={
                "run_id": run_id,
                "evaluation_set": evaluation_set,
                "tiny": tiny,
                "agents": agents,
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
        )
    response.raise_for_status()
    return response.json()


def record_run_claims(run_id: str, claim_ids: list[str]) -> None:
    """Record the claims selected for one dispatch."""
    with backend() as client:
        response = client.put(
            f"/api/runs/{run_id}/claims", json={"claim_ids": claim_ids}
        )
    response.raise_for_status()


def finish_run(run_id: str) -> None:
    """Finish a dispatch after every claim worker has returned."""
    with backend() as client:
        response = client.post(
            f"/api/runs/{run_id}/finish", json={"summary": "Dispatch complete"}
        )
    response.raise_for_status()


def stop_runs() -> None:
    """Stop running dispatches if service execution fails."""
    with backend() as client:
        response = client.post("/api/runs/stop")
    response.raise_for_status()


def start_claim_direct(claim_id: str) -> None:
    """Reserve one waiting claim for a direct claim-agent invocation."""
    with backend() as client:
        response = client.post(f"/api/claims/{claim_id}/run/direct")
    response.raise_for_status()


def save_claim_thread(claim_id: str, thread_id: str) -> str:
    """Save the thread identifier for the active claim attempt."""
    with backend() as client:
        response = client.post(
            f"/api/agent/claims/{claim_id}/thread", json={"thread_id": thread_id}
        )
    response.raise_for_status()
    return response.json()["thread_id"]
