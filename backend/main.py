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
    to_inr,
)
from backend.payout import calculate
from backend.schemas import (
    DecisionInput,
    PaymentInput,
    PayoutInput,
    ProgressInput,
    SignOffInput,
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


def require_in_progress(record: dict[str, Any]) -> None:
    """Reject agent writes until the claim run has started."""
    if record["status"] != "in progress":
        raise HTTPException(status_code=409, detail="Claim is not in progress")


def calculate_or_422(claim_id: str, request: PayoutInput) -> dict[str, Any]:
    """Return a payout calculation or a clear input error."""
    try:
        return calculate(
            claim_id,
            request.benefit,
            [item.model_dump() for item in request.items],
            request.paid_by_others_inr,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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


@app.post("/api/claims/{claim_id}/progress", operation_id="record_claim_progress")
def record_claim_progress(claim_id: str, request: ProgressInput) -> dict[str, str]:
    """Record one step in the agent's claim review."""
    record = claim_or_404(claim_id)
    require_in_progress(record)
    database.add_event(claim_id, request.step, request.detail)
    return {"status": "ok"}


@app.post("/api/agent/claims/{claim_id}/payout", operation_id="calculate_payout")
def calculate_claim_payout(claim_id: str, request: PayoutInput) -> dict[str, Any]:
    """Calculate the policy payout in Indian rupees."""
    claim_or_404(claim_id)
    return calculate_or_422(claim_id, request)


@app.post("/api/claims/{claim_id}/decision", operation_id="record_claim_decision")
def record_claim_decision(claim_id: str, request: DecisionInput) -> dict[str, Any]:
    """Record a policy decision and its backend-calculated payout."""
    record = claim_or_404(claim_id)
    require_in_progress(record)
    payout = calculate_or_422(claim_id, request)
    if request.outcome != "approve":
        payout = {"calculation": [], "payout_inr": 0.0, "needs_sign_off": False}
    decision = {
        "outcome": request.outcome,
        "reason": request.reason,
        "policy_section": request.policy_section,
        **payout,
    }
    status = "waiting for sign-off" if payout["needs_sign_off"] else "decided"
    try:
        database.save_decision(claim_id, decision, status)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(claim_id, "Decision recorded", request.outcome.title())
    return {"decision": decision, "status": status}


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, request: SignOffInput) -> dict[str, str]:
    """Record a handler's sign-off on an over-limit claim."""
    record = claim_or_404(claim_id)
    if record["decision"] is None or record["decision"]["outcome"] != "approve":
        raise HTTPException(
            status_code=409, detail="Only approved claims can be signed off"
        )
    handlers = database.get_setting("handlers")
    if request.handler_id not in {handler["handler_id"] for handler in handlers}:
        raise HTTPException(status_code=422, detail="Unknown handler")
    try:
        database.sign_off_claim(claim_id, request.handler_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(claim_id, "Handler sign-off", request.handler_id)
    return {"status": "decided", "handler_id": request.handler_id}


@app.post("/api/claims/{claim_id}/pay", operation_id="pay_claim")
def pay_claim(claim_id: str, request: PaymentInput) -> dict[str, Any]:
    """Record a simulated payment after approval and required sign-off."""
    record = claim_or_404(claim_id)
    decision = record["decision"]
    if decision is None or decision["outcome"] != "approve":
        raise HTTPException(status_code=409, detail="Only approved claims can be paid")
    if record["payment"] is not None:
        raise HTTPException(status_code=409, detail="Claim has already been paid")
    if round(request.amount_inr, 2) != decision["payout_inr"]:
        raise HTTPException(
            status_code=409, detail="Payment amount must match the approved payout"
        )
    approval_limit = to_inr(database.get_setting("company")["payout_approval_limit"])
    needs_sign_off = decision["payout_inr"] > approval_limit
    if needs_sign_off and record["sign_off_handler_id"] is None:
        raise HTTPException(
            status_code=409, detail="Handler sign-off is required before payment"
        )
    try:
        database.save_payment(claim_id, round(request.amount_inr, 2))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(
        claim_id, "Simulated payment recorded", f"₹{request.amount_inr:,.2f}"
    )
    return {
        "status": "paid",
        "simulated": True,
        "amount_inr": round(request.amount_inr, 2),
    }
