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

LineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]


class PayoutLine(BaseModel):
    """One claimed item or expense, with every calculation input explicit."""

    description: str = Field(min_length=1)
    amount: float = Field(ge=0)
    quantity: int = Field(ge=1)
    kind: LineKind
    nights: int = Field(ge=0)
    hours: float = Field(ge=0)


class LinePayoutInput(BaseModel):
    """Inputs needed to calculate a payout from claim lines."""

    benefit: Benefit
    lines: list[PayoutLine]
    paid_by_others: float = Field(ge=0)


class LineDecisionInput(LinePayoutInput):
    """Decision and policy evidence recorded by the durable claim graph."""

    outcome: Literal["approve", "decline", "refer"]
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)


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


class ThreadInput(BaseModel):
    """Thread identifier assigned to one claim attempt."""

    thread_id: str = Field(min_length=1)


class ModelCallInput(BaseModel):
    """Token and cost usage recorded for one claim model response."""

    run_id: str | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


class WorkTimeInput(BaseModel):
    """Duration recorded for one claim work invocation."""

    run_id: str | None
    seconds: float = Field(ge=0)


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
