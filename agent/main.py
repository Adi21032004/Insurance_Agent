"""The UnderCover Agent and its backend API tools."""

import json
import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from agent.testing import stub_model, stub_over_limit_model, stub_queue_model
from backend.schemas import ItemSelection, RunTodoItemInput

load_dotenv()

BACKEND_URL = os.getenv("UNDERCOVER_BACKEND_URL", "http://127.0.0.1:8000")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
PAYMENT_CONFLICT_STATUS = 409


class DecisionToolInput(BaseModel):
    """Structured decision fields accepted by the agent tool."""

    claim_id: str = Field(min_length=1)
    outcome: Literal["approve", "decline", "refer"]
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)
    benefit: Literal[
        "trip_cancellation",
        "trip_interruption",
        "trip_delay",
        "baggage_delay",
        "baggage",
        "passport",
        "emergency_medical",
    ]
    items: list[ItemSelection]
    paid_by_others_inr: float = Field(ge=0)


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


@tool
def get_claim_context(claim_id: str) -> str:
    """Read the claim, policy, customer history and evidence from the backend."""
    with backend() as client:
        response = client.get(f"/api/agent/claims/{claim_id}")
    response.raise_for_status()
    return json.dumps(response.json())


@tool
def search_claim_knowledge(
    claim_id: str, source: Literal["policy_wording", "claims_manual"], query: str
) -> str:
    """Find relevant sections in the policy wording or claims manual."""
    with backend() as client:
        response = client.post(
            "/api/agent/knowledge/search",
            json={"claim_id": claim_id, "source": source, "query": query},
        )
    response.raise_for_status()
    return json.dumps(response.json()["results"])


@tool
def read_claim_section(
    claim_id: str,
    source: Literal["policy_wording", "claims_manual"],
    section_id: str,
) -> str:
    """Read one relevant source section and record it on the claim."""
    with backend() as client:
        response = client.post(
            "/api/agent/knowledge/read",
            json={"claim_id": claim_id, "source": source, "section_id": section_id},
        )
    response.raise_for_status()
    return json.dumps(response.json())


@tool
def record_claim_progress(claim_id: str, step: str, detail: str) -> str:
    """Record one visible step in the agent's review."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/progress",
            json={"step": step, "detail": detail},
        )
    response.raise_for_status()
    return response.json()["status"]


@tool
def calculate_claim_payout(
    claim_id: str,
    benefit: str,
    items: list[ItemSelection],
    paid_by_others_inr: float,
) -> str:
    """Calculate an INR payout using the policy limits in the backend."""
    with backend() as client:
        response = client.post(
            f"/api/agent/claims/{claim_id}/payout",
            json={
                "benefit": benefit,
                "items": [item.model_dump() for item in items],
                "paid_by_others_inr": paid_by_others_inr,
            },
        )
    response.raise_for_status()
    return json.dumps(response.json())


@tool(args_schema=DecisionToolInput)
def record_claim_decision(
    **kwargs: Any,
) -> str:
    """Store the decision and backend-calculated payout."""
    request = DecisionToolInput.model_validate(kwargs)
    with backend() as client:
        response = client.post(
            f"/api/claims/{request.claim_id}/decision",
            json=request.model_dump(exclude={"claim_id"}),
        )
    response.raise_for_status()
    return json.dumps(response.json())


@tool
def pay_claim(claim_id: str, amount_inr: float) -> str:
    """Request a simulated payment through the guarded backend operation."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/pay", json={"amount_inr": amount_inr}
        )
    if response.status_code == PAYMENT_CONFLICT_STATUS:
        return f"Payment blocked: {response.json()['detail']}"
    response.raise_for_status()
    return json.dumps(response.json())


@tool
def save_run_plan(run_id: str, items: list[RunTodoItemInput]) -> str:
    """Reserve claims and persist the queue agent's ordered to-do list."""
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/select",
            json={"claim_ids": [item.claim_id for item in items]},
        )
        response.raise_for_status()
        response = client.put(
            f"/api/agent/runs/{run_id}/todos",
            json={"items": [item.model_dump() for item in items]},
        )
        response.raise_for_status()
    return json.dumps(response.json())


def model(claim_id: str | None = None) -> BaseChatModel:
    """Return the configured test model or the OpenRouter model."""
    if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1":
        test_claim_id = claim_id or os.getenv("UNDERCOVER_TEST_CLAIM_ID", "CLM-1001")
        if test_claim_id == "CLM-1003":
            return stub_over_limit_model(test_claim_id)
        return stub_model(test_claim_id)
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def queue_model(
    candidate_ids: list[str], max_claims: int, run_id: str
) -> BaseChatModel:
    """Return the configured queue planner model or its deterministic test stub."""
    if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1":
        return stub_queue_model(candidate_ids, max_claims, run_id)
    return model()


def update_run_todo(run_id: str, todo_id: int, status: str) -> None:
    """Persist one queue to-do state through the backend."""
    with backend() as client:
        response = client.patch(
            f"/api/agent/runs/{run_id}/todos/{todo_id}", json={"status": status}
        )
    response.raise_for_status()


def run(claim_id: str, thread_id: str) -> str:
    """Run the agent on one claim using backend data and instructions."""
    with backend() as client:
        response = client.get("/api/instructions")
    response.raise_for_status()
    tools = [
        get_claim_context,
        search_claim_knowledge,
        read_claim_section,
        record_claim_progress,
        calculate_claim_payout,
        record_claim_decision,
        pay_claim,
    ]
    agent = create_agent(
        model(claim_id),
        tools=tools,
        system_prompt=response.json()["claim_instructions"],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Review claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text


def run_queue(run_id: str, thread_id: str) -> str:
    """Plan a capped queue run and review each selected claim in a fresh agent."""
    with backend() as client:
        run_response = client.get(f"/api/agent/runs/{run_id}")
        run_response.raise_for_status()
        run_record = run_response.json()
        claims_response = client.get("/api/claims")
        claims_response.raise_for_status()
        waiting = [
            claim
            for claim in claims_response.json()["claims"]
            if claim["status"] == "waiting"
        ]
    if run_record["run_type"] == "queue" and not run_record["selection_complete"]:
        expected = min(run_record["max_claims"], len(waiting))
        candidates = [claim["claim_id"] for claim in waiting]
        planner = create_agent(
            queue_model(candidates, run_record["max_claims"], run_id),
            tools=[save_run_plan],
            system_prompt=queue_instructions(),
        )
        planner.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "run_id": run_id,
                                "max_claims": run_record["max_claims"],
                                "waiting_claims": waiting,
                                "required_count": expected,
                            }
                        ),
                    }
                ]
            },
            config={"configurable": {"thread_id": thread_id}},
        )
    with backend() as client:
        run_response = client.get(f"/api/agent/runs/{run_id}")
        run_response.raise_for_status()
        run_record = run_response.json()
    if not run_record["selection_complete"]:
        raise RuntimeError("The queue agent did not save its plan")
    expected = min(run_record["max_claims"], len(waiting))
    if run_record["run_type"] == "queue" and len(run_record["todos"]) != expected:
        raise RuntimeError("The queue agent did not select the full capped batch")

    outcomes = {"approve": 0, "decline": 0, "refer": 0, "failed": 0}
    for todo in run_record["todos"]:
        update_run_todo(run_id, todo["todo_id"], "in progress")
        database_detail = run_claim_helper(
            todo["claim_id"], f"{thread_id}-{todo['claim_id']}-attempt1"
        )
        outcome = decision_outcome(todo["claim_id"])
        if outcome is None:
            raise RuntimeError(f"Claim helper saved no decision for {todo['claim_id']}")
        update_run_todo(run_id, todo["todo_id"], "done")
        outcomes[outcome] += 1
        if not database_detail:
            raise RuntimeError("The claim helper returned no report")

    summary = (
        f"Reviewed {len(run_record['todos'])} claims: {outcomes['approve']} approved, "
        f"{outcomes['decline']} declined, {outcomes['refer']} referred, "
        f"{outcomes['failed']} need handler attention."
    )
    with backend() as client:
        response = client.post(
            f"/api/agent/runs/{run_id}/complete", json={"summary": summary}
        )
    response.raise_for_status()
    return summary


def queue_instructions() -> str:
    """Read queue instructions from the backend."""
    with backend() as client:
        response = client.get("/api/instructions")
    response.raise_for_status()
    return response.json()["queue_instructions"]


def run_claim_helper(claim_id: str, thread_id: str) -> str:
    """Create a fresh claim agent and run it on one selected claim."""
    return run(claim_id, thread_id)


def decision_outcome(claim_id: str) -> str | None:
    """Read a claim's recorded outcome after its helper returns."""
    with backend() as client:
        response = client.get(f"/api/agent/claims/{claim_id}")
    response.raise_for_status()
    decision = response.json()["decision"]
    return decision["outcome"] if decision is not None else None
