"""Claim tools backed by the UnderCover HTTP API."""

import json
from typing import Annotated, Literal, TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent import api
from backend.schemas import Benefit, LineDecisionInput, PayoutLine


class ClaimContext(TypedDict):
    """The claim id supplied to a graph run, never chosen by the model."""

    claim_id: str


Source = Literal["policy_wording", "claims_manual"]


def _result(runtime: ToolRuntime[ClaimContext], name: str, value: object) -> str:
    """Record a short result event and return the full tool response."""
    api.record_progress(
        runtime.context["claim_id"], "Tool result", f"{name} returned information."
    )
    return json.dumps(value)


@tool
def get_claim(runtime: ToolRuntime[ClaimContext]) -> str:
    """Read the claim, evidence, policy and customer context."""
    return _result(runtime, "get_claim", api.get_claim(runtime.context["claim_id"]))


@tool
def get_policy(runtime: ToolRuntime[ClaimContext]) -> str:
    """Read the customer's policy and coverage."""
    return _result(runtime, "get_policy", api.get_policy(runtime.context["claim_id"]))


@tool
def get_claim_history(runtime: ToolRuntime[ClaimContext]) -> str:
    """Read the customer's claim history."""
    return _result(
        runtime,
        "get_claim_history",
        api.get_claim_history(runtime.context["claim_id"]),
    )


@tool
def search_documents(
    source: Source,
    query: Annotated[str, "Words to find in the source"],
    runtime: ToolRuntime[ClaimContext],
) -> str:
    """Find relevant sections in the policy wording or claims manual."""
    return _result(
        runtime,
        "search_documents",
        api.search_documents(runtime.context["claim_id"], source, query),
    )


@tool
def read_section(
    source: Source,
    section_id: Annotated[str, "The section identifier returned by search_documents"],
    runtime: ToolRuntime[ClaimContext],
) -> str:
    """Read and record one relevant source section."""
    return _result(
        runtime,
        "read_section",
        api.read_section(runtime.context["claim_id"], source, section_id),
    )


@tool
def calculate_payout(
    benefit: Benefit,
    lines: list[PayoutLine],
    paid_by_others: Annotated[float, "USD another party paid or refunded"],
    runtime: ToolRuntime[ClaimContext],
) -> str:
    """Calculate an INR payout using the backend's policy rules."""
    result = api.calculate_payout(
        runtime.context["claim_id"],
        benefit,
        [line.model_dump() for line in lines],
        paid_by_others,
    )
    return _result(runtime, "calculate_payout", result)


@tool(args_schema=LineDecisionInput)
def record_decision(runtime: ToolRuntime[ClaimContext], **decision: object) -> Command:
    """Persist the claim decision and its backend-calculated payout."""
    result = api.record_decision(
        runtime.context["claim_id"],
        LineDecisionInput.model_validate(decision).model_dump(),
    )
    encoded = json.dumps(result)
    return Command(
        update={
            "decision": result["decision"],
            "status": result["status"],
            "messages": [ToolMessage(encoded, tool_call_id=runtime.tool_call_id)],
        }
    )


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    search_documents,
    read_section,
    calculate_payout,
    record_decision,
]
