"""The UnderCover backend."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent.instructions import CLAIM_INSTRUCTIONS
from backend import agent_service, database, runs
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
from backend.payout import (
    LinePayout,
    PayoutCoverage,
    SelectedItemsPayout,
    calculate_items,
    calculate_lines,
)
from backend.schemas import (
    DecisionInput,
    KnowledgeReadInput,
    KnowledgeSearchInput,
    LineDecisionInput,
    LinePayoutInput,
    ModelCallInput,
    PaymentInput,
    PayoutInput,
    ProgressInput,
    SignOffInput,
    ThreadInput,
    WorkTimeInput,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the local claims database before serving requests."""
    database.initialize_database()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)
app.include_router(runs.router)


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
    decision = dict(record["decision"]) if record["decision"] is not None else None
    sign_off_handler = next(
        (
            handler
            for handler in database.get_setting("handlers")
            if handler["handler_id"] == record["sign_off_handler_id"]
        ),
        None,
    )
    if decision is not None:
        decision["signed_off_by"] = (
            sign_off_handler["name"] if sign_off_handler is not None else None
        )
        decision["signed_off_at"] = record["sign_off_at"]
    payment = dict(record["payment"]) if record["payment"] is not None else None
    if payment is not None:
        payment["signed_off_by"] = payment.get("signed_off_by")
        payment["signed_off_at"] = record["sign_off_at"]
    return {
        "claim": claim,
        "evaluation_id": record["evaluation_id"],
        "copy_of": record["copy_of"],
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
        "decision": decision,
        "sign_off_handler_id": record["sign_off_handler_id"],
        "sign_off_at": record["sign_off_at"],
        "payment": payment,
        "thread_id": record["thread_id"],
        "failure_count": record["failure_count"],
        "progress": database.list_events(record["claim"]["claim_id"]),
        "source_reads": database.list_source_reads(record["claim"]["claim_id"]),
    }


def agent_claim_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build agent context without handing over either full source document."""
    payload = claim_payload(record)
    del payload["policy_wording"]
    for item, source_item in zip(
        payload["claim"]["items"], record["claim"]["items"], strict=True
    ):
        item["amount_usd"] = source_item["amount"]
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


def calculate(
    claim_id: str,
    benefit: str,
    items: list[dict[str, Any]],
    paid_by_others_inr: float,
) -> dict[str, Any]:
    """Calculate selected claim items with backend policy settings."""
    record = claim_or_404(claim_id)
    company = database.get_setting("company")
    plans = database.get_setting("plans")
    rate = company["usd_to_inr_rate"]
    plan = next(
        row
        for row in converted_plans(plans, rate)["plans"]
        if row["plan"] == record["policy"]["plan"]
    )
    electronics_limit = next(
        row["electronics_per_item_limit"]
        for row in plans["add_ons"]
        if row["add_on"] == "electronics_plus"
    )
    return calculate_items(
        SelectedItemsPayout(
            claim=record,
            coverage=PayoutCoverage(
                plan,
                record["policy"]["add_ons"],
                rate,
                plans["deductible_applies_to"],
                electronics_limit,
            ),
            approval_limit_usd=company["payout_approval_limit"],
            benefit=benefit,
            selections=items,
            paid_by_others_inr=paid_by_others_inr,
        )
    )


def calculate_or_422(claim_id: str, request: PayoutInput) -> dict[str, Any]:
    """Return a payout calculation or a clear input error."""
    try:
        record = claim_or_404(claim_id)
        if request.benefit != record["claim"]["claim_type"]:
            raise ValueError("Benefit does not match the claim type")
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


def calculate_lines_or_422(
    record: dict[str, Any], request: LinePayoutInput
) -> dict[str, Any]:
    """Return the USD payout calculation for explicit claim lines."""
    company = database.get_setting("company")
    try:
        if request.benefit != record["claim"]["claim_type"]:
            raise ValueError("Benefit does not match the claim type")
        return calculate_lines(
            LinePayout(
                policy=record["policy"],
                plan_settings=database.get_setting("plans"),
                approval_limit_usd=company["payout_approval_limit"],
                benefit=request.benefit,
                lines=[line.model_dump() for line in request.lines],
                paid_by_others=request.paid_by_others,
            )
        )
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
    """Return the read-only claim agent instructions."""
    return {"claim": CLAIM_INSTRUCTIONS}


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


@app.post("/api/agent/claims/{claim_id}/thread", operation_id="set_claim_thread")
def set_claim_thread(claim_id: str, request: ThreadInput) -> dict[str, str]:
    """Save the thread id assigned to the current claim attempt."""
    claim_or_404(claim_id)
    try:
        thread_id = database.set_claim_thread_id(claim_id, request.thread_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"thread_id": thread_id}


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
def start_claim(claim_id: str) -> dict[str, str]:
    """Start the agent review for a waiting claim."""
    record = claim_or_404(claim_id)
    if record["status"] != "waiting":
        raise HTTPException(status_code=409, detail="Claim is not waiting")
    try:
        database.start_direct_claim(claim_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        agent_service.start_claim(claim_id)
    except agent_service.AgentServiceConflict as error:
        database.set_claim_status(claim_id, "waiting")
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RuntimeError as error:
        database.set_claim_status(claim_id, "waiting")
        raise HTTPException(status_code=503, detail=str(error)) from error
    database.add_event(
        claim_id, "Review started", "Checking the story, policy and documents."
    )
    return {"status": "in progress"}


@app.post("/api/claims/{claim_id}/run/direct", operation_id="start_direct_claim_run")
def start_direct_claim_run(claim_id: str) -> dict[str, str]:
    """Reserve a waiting claim for an untracked direct agent run."""
    try:
        database.start_direct_claim(claim_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Claim not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(claim_id, "Review started", "Assigned to a direct claim run.")
    return {"status": "in progress"}


@app.post("/api/claims/{claim_id}/progress", operation_id="record_claim_progress")
def record_claim_progress(claim_id: str, request: ProgressInput) -> dict[str, str]:
    """Record one step in the agent's claim review."""
    record = claim_or_404(claim_id)
    require_in_progress(record)
    database.add_event(claim_id, request.step, request.detail)
    return {"status": "ok"}


@app.post(
    "/api/claims/{claim_id}/model-calls",
    operation_id="record_model_call",
)
def record_model_call(claim_id: str, request: ModelCallInput) -> dict[str, str]:
    """Record the token usage and cost of one claim model call."""
    record = claim_or_404(claim_id)
    if record["status"] == "waiting":
        raise HTTPException(status_code=409, detail="Claim has not been started")
    if request.run_id is not None and database.get_run(request.run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    database.record_model_call(
        claim_id,
        request.input_tokens,
        request.output_tokens,
        request.cost,
        request.run_id,
        record["thread_id"],
    )
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/work-times",
    operation_id="record_work_time",
)
def record_work_time(claim_id: str, request: WorkTimeInput) -> dict[str, str]:
    """Record the duration of one claim-agent invocation."""
    record = claim_or_404(claim_id)
    if record["status"] == "waiting":
        raise HTTPException(status_code=409, detail="Claim has not been started")
    if request.run_id is not None and database.get_run(request.run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    database.record_work_time(
        claim_id,
        request.seconds,
        request.run_id,
        record["thread_id"],
    )
    return {"status": "recorded"}


@app.post(
    "/api/agent/claims/{claim_id}/payout",
    operation_id="calculate_selected_item_payout",
)
def calculate_claim_payout(claim_id: str, request: PayoutInput) -> dict[str, Any]:
    """Calculate the policy payout in Indian rupees."""
    claim_or_404(claim_id)
    return calculate_or_422(claim_id, request)


@app.post("/api/claims/{claim_id}/payout", operation_id="calculate_payout")
def calculate_claim_lines(claim_id: str, request: LinePayoutInput) -> dict[str, Any]:
    """Calculate a payout from explicit claim lines."""
    record = claim_or_404(claim_id)
    return calculate_lines_or_422(record, request)


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_claim_decision(
    claim_id: str, request: LineDecisionInput | DecisionInput
) -> dict[str, Any]:
    """Record a policy decision and its backend-calculated payout."""
    record = claim_or_404(claim_id)
    had_decision = record["decision"] is not None
    if not had_decision:
        require_in_progress(record)
    if isinstance(request, LineDecisionInput):
        payout = calculate_lines_or_422(record, request)
    else:
        payout = calculate_or_422(claim_id, request)
    if request.outcome != "approve":
        payout = {
            "calculation": [],
            "payout": 0.0,
            "payout_inr": 0.0,
            "needs_sign_off": False,
        }
    if isinstance(request, LineDecisionInput):
        rate = database.get_setting("company")["usd_to_inr_rate"]
        payout = {
            "calculation": [
                {
                    "step": line["step"],
                    "amount_inr": to_inr(line["amount"], rate),
                }
                for line in payout["calculation"]
            ],
            "payout": payout["payout"],
            "payout_inr": to_inr(payout["payout"], rate),
            "needs_sign_off": payout["needs_sign_off"],
        }
    else:
        rate = database.get_setting("company")["usd_to_inr_rate"]
        payout["payout"] = round(payout["payout_inr"] / rate, 2)
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
    status = (
        "waiting for sign-off"
        if request.outcome == "approve" and payout["needs_sign_off"]
        else "in progress"
        if request.outcome == "approve"
        else "decided"
    )
    try:
        database.save_decision(claim_id, decision, status)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if had_decision:
        saved = claim_or_404(claim_id)
        return {"decision": saved["decision"], "status": saved["status"]}
    database.add_event(claim_id, "Decision recorded", request.outcome.title())
    return {"decision": decision, "status": status}


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(
    claim_id: str, request: SignOffInput, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Record a handler's sign-off on an over-limit claim."""
    record = claim_or_404(claim_id)
    if record["evaluation_id"] is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Claim {claim_id} is a copy in an evaluation and can't be signed off"
            ),
        )
    if record["decision"] is None or record["decision"]["outcome"] != "approve":
        raise HTTPException(
            status_code=409, detail="Only approved claims can be signed off"
        )
    handlers = database.get_setting("handlers")
    handler = next(
        (item for item in handlers if item["handler_id"] == request.handler_id),
        None,
    )
    if handler is None:
        raise HTTPException(status_code=404, detail="Handler not found")
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            status_code=403, detail="Handler cannot approve over-limit claims"
        )
    try:
        signed_off_at = database.sign_off_claim(claim_id, request.handler_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    database.add_event(claim_id, "Handler sign-off", request.handler_id)
    background_tasks.add_task(agent_service.resume_claim, claim_id)
    return {
        "status": "in progress",
        "handler_id": request.handler_id,
        "signed_off_at": signed_off_at,
    }


@app.post("/api/claims/{claim_id}/pay", operation_id="record_simulated_payment")
def pay_claim(claim_id: str, request: PaymentInput) -> dict[str, Any]:
    """Record a simulated payment after approval and required sign-off."""
    record = claim_or_404(claim_id)
    decision = record["decision"]
    if decision is None or decision["outcome"] != "approve":
        raise HTTPException(status_code=409, detail="Only approved claims can be paid")
    if round(request.amount_inr, 2) != decision["payout_inr"]:
        raise HTTPException(
            status_code=422, detail="Payment amount must match the approved payout"
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
    approval_limit_usd = company_settings["payout_approval_limit"]
    approval_limit = to_inr(approval_limit_usd, company_settings["usd_to_inr_rate"])
    needs_sign_off = decision["payout_inr"] > approval_limit
    if needs_sign_off and record["sign_off_handler_id"] is None:
        raise HTTPException(
            status_code=403,
            detail=(
                f"A payout over ${approval_limit_usd:,.2f} needs a handler's sign-off"
            ),
        )
    handler_name = next(
        (
            handler["name"]
            for handler in database.get_setting("handlers")
            if handler["handler_id"] == record["sign_off_handler_id"]
        ),
        None,
    )
    try:
        database.save_payment(
            claim_id,
            round(request.amount_inr, 2),
            handler_name,
            record["sign_off_at"],
        )
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


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim_for_graph(claim_id: str, request: PaymentInput) -> dict[str, Any]:
    """Record a simulated payment and return the updated claim."""
    pay_claim(claim_id, request)
    return claim_payload(claim_or_404(claim_id))
