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
