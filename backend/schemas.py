"""Required request fields for the pilot APIs."""

from typing import Literal

from pydantic import BaseModel, Field

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]


class ProgressInput(BaseModel):
    """One progress event written by the agent."""

    step: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class ItemSelection(BaseModel):
    """One claim item selected for payout calculation."""

    item_index: int = Field(ge=0)


class PayoutInput(BaseModel):
    """Inputs needed to calculate an approved payout."""

    benefit: Benefit
    items: list[ItemSelection]
    paid_by_others_inr: float = Field(ge=0)


class DecisionInput(PayoutInput):
    """Decision and policy evidence recorded for a claim."""

    outcome: Literal["approve", "decline", "refer"]
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)


class SignOffInput(BaseModel):
    """Handler who signs off an over-limit payout."""

    handler_id: str = Field(min_length=1)


class PaymentInput(BaseModel):
    """Exact INR amount to record as a simulated payment."""

    amount_inr: float = Field(ge=0)


class RunStartInput(BaseModel):
    """Maximum number of distinct waiting claims in a queue run."""

    max_claims: int = Field(ge=1, le=25)


class RunTodoItemInput(BaseModel):
    """One claim and description in the queue agent's work plan."""

    claim_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class RunTodoPlanInput(BaseModel):
    """Ordered queue agent work items for one run."""

    items: list[RunTodoItemInput]


class RunSelectionInput(BaseModel):
    """Claim identifiers reserved for a run in the requested order."""

    claim_ids: list[str]


class RunTodoStatusInput(BaseModel):
    """Allowed state for one queue run work item."""

    status: Literal["to do", "in progress", "done", "left for handler"]


class KnowledgeSearchInput(BaseModel):
    """Source and query used to find relevant handling sections."""

    claim_id: str = Field(min_length=1)
    source: Literal["policy_wording", "claims_manual"]
    query: str = Field(min_length=1)


class KnowledgeReadInput(BaseModel):
    """Claim and section identifier for one source read."""

    claim_id: str = Field(min_length=1)
    source: Literal["policy_wording", "claims_manual"]
    section_id: str = Field(min_length=1)
