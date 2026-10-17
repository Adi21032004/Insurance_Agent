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

from agent.testing import stub_model

load_dotenv()

BACKEND_URL = os.getenv("UNDERCOVER_BACKEND_URL", "http://127.0.0.1:8000")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
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
    items: list[dict[str, int]]
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
def get_policy_wording() -> str:
    """Read the policy wording from the backend in Indian rupees."""
    with backend() as client:
        response = client.get("/api/policy-wording")
    response.raise_for_status()
    return response.json()["policy_wording"]


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
    items: list[dict[str, int]],
    paid_by_others_inr: float,
) -> str:
    """Calculate an INR payout using the policy limits in the backend."""
    with backend() as client:
        response = client.post(
            f"/api/agent/claims/{claim_id}/payout",
            json={
                "benefit": benefit,
                "items": items,
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


def model() -> BaseChatModel:
    """Return the configured test model or the OpenRouter model."""
    if os.getenv("UNDERCOVER_AGENT_TEST_MODE") == "1":
        return stub_model(os.getenv("UNDERCOVER_TEST_CLAIM_ID", "CLM-1001"))
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def run(claim_id: str, thread_id: str) -> str:
    """Run the agent on one claim using backend data and instructions."""
    with backend() as client:
        response = client.get("/api/instructions")
    response.raise_for_status()
    tools = [
        get_claim_context,
        get_policy_wording,
        record_claim_progress,
        calculate_claim_payout,
        record_claim_decision,
        pay_claim,
    ]
    agent = create_agent(
        model(), tools=tools, system_prompt=response.json()["instructions"]
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Review claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text
