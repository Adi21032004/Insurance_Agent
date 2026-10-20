"""The UnderCover backend."""

import os
from contextlib import asynccontextmanager
from threading import Thread
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent.instructions import CLAIM_INSTRUCTIONS, QUEUE_INSTRUCTIONS
from agent.main import run_queue as run_queue_agent
from backend import database
from backend.knowledge import read_section, search_sections, split_sections
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
    KnowledgeReadInput,
    KnowledgeSearchInput,
    PaymentInput,
    PayoutInput,
    ProgressInput,
    RunAttemptFailureInput,
    RunCompleteInput,
    RunRetryInput,
    RunSelectionInput,
    RunStartInput,
    RunTodoPlanInput,
    RunTodoStatusInput,
    SignOffInput,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the local claims database before serving requests."""
    database.initialize_database()
    dispatch_recovered_runs()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def claim_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build the INR claim context shown to handlers and the agent."""
    company_settings = database.get_setting("company")
    rate = company_settings["usd_to_inr_rate"]
    plan_data = converted_plans(database.get_setting("plans"), rate)
    plan = next(
        row for row in plan_data["plans"] if row["plan"] == record["policy"]["plan"]
    )
    policy = converted_policy(record["policy"], rate)
    claim = converted_claim(record["claim"], rate)
    company = converted_company(company_settings)
    return {
        "claim": claim,
        "policy": policy,
        "customer": converted_customer(record["customer"], rate),
        "plan": plan,
        "add_ons": plan_data["add_ons"],
        "company": company,
        "handlers": database.get_setting("handlers"),
        "policy_wording": converted_strings(
            database.get_setting("policy_wording"), rate
        ),
        "status": record["status"],
        "decision": record["decision"],
        "sign_off_handler_id": record["sign_off_handler_id"],
        "payment": record["payment"],
        "failure_count": record["failure_count"],
        "progress": database.list_events(record["claim"]["claim_id"]),
        "source_reads": database.list_source_reads(record["claim"]["claim_id"]),
    }


def agent_claim_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build agent context without handing over either full source document."""
    payload = claim_payload(record)
    del payload["policy_wording"]
    return payload


def knowledge_document(source: str) -> str:
    """Return one approved knowledge source from backend storage."""
    return database.get_setting(source)


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


def process_queue_run(run_id: str) -> None:
    """Run the queue agent and recover any run-level failure."""
    try:
        run_queue_agent(run_id, f"{run_id}-{uuid4()}")
    except Exception:
        run = database.get_run(run_id)
        if run is None or run["status"] != "in progress":
            return
        continuation_id = database.recover_expired_run(
            run_id, run_lease_seconds(), force=True
        )
        if continuation_id is not None:
            process_queue_run(continuation_id)


def run_lease_seconds() -> int:
    """Read the configured heartbeat lease in seconds."""
    return max(30, int(os.getenv("UNDERCOVER_RUN_LEASE_SECONDS", "300")))


def dispatch_recovered_runs() -> list[str]:
    """Recover expired runs and start their continuation workers."""
    continuation_ids = []
    for run_id in database.expired_run_ids(run_lease_seconds()):
        continuation_id = database.recover_expired_run(run_id, run_lease_seconds())
        if continuation_id is not None:
            continuation_ids.append(continuation_id)
    for run_id in continuation_ids:
        Thread(target=process_queue_run, args=(run_id,), daemon=True).start()
    return continuation_ids


def create_run_for_claim(claim_id: str) -> str:
    """Create a single-claim run and its first to-do item."""
    run_id = str(uuid4())
    database.create_run(run_id, "claim", 1)
    database.select_run_claims(run_id, [claim_id])
    database.add_event(claim_id, "Review started", f"Assigned to run {run_id}.")
    return run_id


def active_run_conflict() -> HTTPException:
    """Build a conflict response that identifies the active run."""
    run = database.get_current_run()
    return HTTPException(
        status_code=409,
        detail={
            "message": "Another agent run is already active",
            "run_id": run["run_id"] if run is not None else None,
        },
    )


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
    rate = database.get_setting("company")["usd_to_inr_rate"]
    claims = []
    for record in database.list_claim_records():
        claim = converted_claim(record["claim"], rate)
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
    return agent_claim_payload(claim_or_404(claim_id))


@app.get("/api/instructions", operation_id="get_instructions")
def get_instructions() -> dict[str, str]:
    """Return the read-only queue and claim agent instructions."""
    return {
        "queue_instructions": QUEUE_INSTRUCTIONS,
        "claim_instructions": CLAIM_INSTRUCTIONS,
    }


@app.post("/api/agent/knowledge/search", operation_id="search_knowledge")
def search_knowledge(request: KnowledgeSearchInput) -> dict[str, Any]:
    """Search the requested policy or manual for claim-relevant sections."""
    record = claim_or_404(request.claim_id)
    require_in_progress(record)
    rate = database.get_setting("company")["usd_to_inr_rate"]
    results = search_sections(
        request.source, knowledge_document(request.source), request.query, rate
    )
    return {"results": results}


@app.post("/api/agent/knowledge/read", operation_id="read_knowledge_section")
def read_knowledge_section(request: KnowledgeReadInput) -> dict[str, str]:
    """Read and record one requested policy or manual section."""
    record = claim_or_404(request.claim_id)
    require_in_progress(record)
    rate = database.get_setting("company")["usd_to_inr_rate"]
    section = read_section(
        request.source,
        knowledge_document(request.source),
        request.section_id,
        rate,
    )
    if section is None:
        raise HTTPException(status_code=404, detail="Knowledge section not found")
    database.record_source_read(request.claim_id, section)
    database.add_event(
        request.claim_id,
        "Source section read",
        f"{section['source']}: {section['heading']}",
    )
    return section


@app.post("/api/agent/runs", operation_id="start_queue_run")
def start_queue_run(
    request: RunStartInput, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Start a capped run for waiting claims."""
    dispatch_recovered_runs()
    if database.get_current_run() is not None:
        raise active_run_conflict()
    run_id = str(uuid4())
    try:
        database.create_run(run_id, "queue", request.max_claims)
    except ValueError as error:
        raise active_run_conflict() from error
    background_tasks.add_task(process_queue_run, run_id)
    return {
        "run_id": run_id,
        "status": "in progress",
        "max_claims": request.max_claims,
    }


@app.get("/api/agent/runs/current", operation_id="get_current_run")
def get_current_run() -> dict[str, Any]:
    """Return the active run and its work items, if one exists."""
    return {"run": database.get_current_run()}


@app.get("/api/agent/runs/{run_id}", operation_id="get_run")
def get_run(run_id: str) -> dict[str, Any]:
    """Return one queue run and its work items."""
    run = database.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/agent/runs/{run_id}/select", operation_id="select_run_claims")
def select_run_claims(run_id: str, request: RunSelectionInput) -> dict[str, Any]:
    """Reserve waiting claims for an active run in the requested order."""
    try:
        database.select_run_claims(run_id, request.claim_ids)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Run or claim not found") from error
    except ValueError as error:
        status_code = 409 if "not waiting" in str(error) else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    for claim_id in request.claim_ids:
        database.add_event(claim_id, "Review started", f"Assigned to run {run_id}.")
    return {"status": "selected", "claim_ids": request.claim_ids}


@app.put("/api/agent/runs/{run_id}/todos", operation_id="plan_run_todos")
def plan_run_todos(run_id: str, request: RunTodoPlanInput) -> dict[str, str]:
    """Store the queue agent's ordered plan for a run."""
    try:
        database.plan_run_todos(run_id, [item.model_dump() for item in request.items])
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"status": "ok"}


@app.post("/api/agent/runs/{run_id}/complete", operation_id="complete_run")
def complete_run(run_id: str, request: RunCompleteInput) -> dict[str, str]:
    """Store a handler summary and close a completed queue run."""
    run = database.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] != "in progress":
        raise HTTPException(status_code=409, detail="Run is not active")
    error = run_completion_error(run)
    if error is not None:
        raise HTTPException(status_code=409, detail=error)
    database.finish_run(run_id, request.summary)
    return {"status": "completed", "summary": request.summary}


def run_completion_error(run: dict[str, Any]) -> str | None:
    """Check persisted selection, task and claim state before closing a run."""
    if not run["selection_complete"]:
        return "Run has no completed selection"
    if len(run["todos"]) != run["required_claims"]:
        return "Run selection is incomplete"
    if any(item["status"] not in {"done", "left for handler"} for item in run["todos"]):
        return "Run still has unfinished work"
    return next(
        (
            error
            for error in (claim_completion_error(item) for item in run["todos"])
            if error is not None
        ),
        None,
    )


def claim_completion_error(todo: dict[str, Any]) -> str | None:
    """Check that one task's saved claim state is terminal."""
    record = database.get_claim_record(todo["claim_id"])
    if record is None:
        return "A selected claim is missing"
    if record["status"] in {"waiting", "in progress"}:
        return "A selected claim is unresolved"
    if todo["status"] == "done" and record["decision"] is None:
        return "A completed claim has no decision"
    if todo["status"] == "left for handler" and record["status"] != "needs handler":
        return "Claim handler state is missing"
    return None


@app.post(
    "/api/agent/runs/{run_id}/claims/{claim_id}/failure",
    operation_id="record_claim_attempt_failure",
)
def record_claim_attempt_failure(
    run_id: str, claim_id: str, request: RunAttemptFailureInput
) -> dict[str, Any]:
    """Persist a failed claim attempt and its retry decision."""
    try:
        result = database.fail_claim_attempt(run_id, claim_id, request.detail)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Run claim not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result


@app.post(
    "/api/agent/runs/{run_id}/claims/{claim_id}/retry",
    operation_id="retry_claim_attempt",
)
def retry_claim_attempt(
    run_id: str, claim_id: str, request: RunRetryInput
) -> dict[str, Any]:
    """Reacquire a claim for another attempt in the same run."""
    try:
        attempt = database.retry_claim_attempt(run_id, claim_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Run claim not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(claim_id, "Attempt started", request.reason)
    return {"status": "in progress", "attempt": attempt}


@app.patch("/api/agent/runs/{run_id}/todos/{todo_id}", operation_id="update_run_todo")
def update_run_todo(
    run_id: str, todo_id: int, request: RunTodoStatusInput
) -> dict[str, str]:
    """Update one queue run work item."""
    run = database.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if todo_id not in {item["todo_id"] for item in run["todos"]}:
        raise HTTPException(status_code=404, detail="Run work item not found")
    todo = next(item for item in run["todos"] if item["todo_id"] == todo_id)
    record = database.get_claim_record(todo["claim_id"])
    if record is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if request.status == "in progress" and record["status"] != "in progress":
        raise HTTPException(
            status_code=409, detail="Claim is not reserved for this run"
        )
    if request.status == "done" and record["decision"] is None:
        raise HTTPException(status_code=409, detail="Claim has no saved decision")
    if request.status == "left for handler" and record["status"] != "needs handler":
        raise HTTPException(
            status_code=409, detail="Claim does not need handler review"
        )
    database.set_todo_status(todo_id, request.status)
    if request.status == "in progress" and todo["status"] != "in progress":
        database.add_event(
            todo["claim_id"],
            "Attempt started",
            f"Attempt {todo['attempt_count'] + 1}.",
        )
    return {"status": request.status}


@app.get("/api/policy-wording", operation_id="get_policy_wording")
def get_policy_wording() -> dict[str, str]:
    """Return the policy wording with all currency amounts in INR."""
    rate = database.get_setting("company")["usd_to_inr_rate"]
    return {
        "policy_wording": converted_strings(
            database.get_setting("policy_wording"), rate
        )
    }


@app.post("/api/claims/{claim_id}/run", operation_id="start_claim")
def start_claim(claim_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Start the agent review for a waiting claim."""
    dispatch_recovered_runs()
    if database.get_current_run() is not None:
        raise active_run_conflict()
    record = claim_or_404(claim_id)
    if record["status"] != "waiting":
        raise HTTPException(status_code=409, detail="Claim is not waiting")
    try:
        run_id = create_run_for_claim(claim_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    background_tasks.add_task(process_queue_run, run_id)
    return {"status": "in progress"}


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
    if record["decision"] is None:
        require_in_progress(record)
    payout = calculate_or_422(claim_id, request)
    if request.outcome != "approve":
        payout = {"calculation": [], "payout_inr": 0.0, "needs_sign_off": False}
    decision = {
        "outcome": request.outcome,
        "reason": request.reason,
        "policy_section": request.policy_section,
        "manual_version": split_sections(
            "claims_manual", knowledge_document("claims_manual")
        )[0]["version"],
        "sections_read": [
            {
                "source": source_read["source"],
                "section_id": source_read["section_id"],
                "heading": source_read["heading"],
                "version": source_read["version"],
            }
            for source_read in database.list_source_reads(claim_id)
        ],
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
    if round(request.amount_inr, 2) != decision["payout_inr"]:
        raise HTTPException(
            status_code=409, detail="Payment amount must match the approved payout"
        )
    if record["payment"] is not None:
        if round(record["payment"]["amount_inr"], 2) == round(request.amount_inr, 2):
            return {
                "status": "paid",
                "simulated": True,
                "amount_inr": round(request.amount_inr, 2),
            }
        raise HTTPException(status_code=409, detail="Claim has already been paid")
    company_settings = database.get_setting("company")
    approval_limit = to_inr(
        company_settings["payout_approval_limit"], company_settings["usd_to_inr_rate"]
    )
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
