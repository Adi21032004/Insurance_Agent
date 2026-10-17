"""The UnderCover backend."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from agent.instructions import SYSTEM_PROMPT
from backend import database
from backend.money import (
    converted_claim,
    converted_company,
    converted_customer,
    converted_plans,
    converted_policy,
    converted_strings,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the local claims database before serving requests."""
    database.initialize_database()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def claim_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build the INR claim context shown to handlers and the agent."""
    plan_data = converted_plans(database.get_setting("plans"))
    plan = next(
        row for row in plan_data["plans"] if row["plan"] == record["policy"]["plan"]
    )
    policy = converted_policy(record["policy"])
    claim = converted_claim(record["claim"])
    company = converted_company(database.get_setting("company"))
    return {
        "claim": claim,
        "policy": policy,
        "customer": converted_customer(record["customer"]),
        "plan": plan,
        "add_ons": plan_data["add_ons"],
        "company": company,
        "handlers": database.get_setting("handlers"),
        "policy_wording": converted_strings(database.get_setting("policy_wording")),
        "status": record["status"],
        "decision": record["decision"],
        "sign_off_handler_id": record["sign_off_handler_id"],
        "payment": record["payment"],
        "progress": database.list_events(record["claim"]["claim_id"]),
    }


def claim_or_404(claim_id: str) -> dict[str, Any]:
    """Return a claim record or raise a not-found response."""
    record = database.get_claim_record(claim_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return record


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims() -> dict[str, Any]:
    """List claims in the handler queue."""
    claims = []
    for record in database.list_claim_records():
        claim = converted_claim(record["claim"])
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "claim_type": claim["claim_type"],
                "summary": claim["summary"],
                "amount_claimed_inr": claim["amount_claimed_inr"],
                "status": record["status"],
                "decision": record["decision"],
            }
        )
    return {"claims": claims}


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str) -> dict[str, Any]:
    """Return the claim and its full policy context."""
    return claim_payload(claim_or_404(claim_id))


@app.get("/api/agent/claims/{claim_id}", operation_id="get_agent_claim")
def get_agent_claim(claim_id: str) -> dict[str, Any]:
    """Return the claim context used by the agent."""
    return claim_payload(claim_or_404(claim_id))


@app.get("/api/instructions", operation_id="get_instructions")
def get_instructions() -> dict[str, str]:
    """Return the read-only UnderCover Agent instructions."""
    return {"instructions": SYSTEM_PROMPT}


@app.get("/api/policy-wording", operation_id="get_policy_wording")
def get_policy_wording() -> dict[str, str]:
    """Return the policy wording with all currency amounts in INR."""
    return {"policy_wording": converted_strings(database.get_setting("policy_wording"))}
