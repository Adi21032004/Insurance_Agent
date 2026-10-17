"""Deterministic LangChain model used by automated tests and browser checks."""

from collections.abc import Iterator

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class StubModel(GenericFakeChatModel):
    """A chat model that emits one scripted claim workflow."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool_message(name: str, args: dict[str, object], call_id: str) -> AIMessage:
    """Build one scripted tool call for the fake model."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def stub_model(claim_id: str) -> StubModel:
    """Return a fake model that approves the first queue claim."""
    payout_request = {
        "claim_id": claim_id,
        "benefit": "trip_delay",
        "items": [{"item_index": 0}, {"item_index": 1}, {"item_index": 2}],
        "paid_by_others_inr": 0,
    }
    decision_request = {
        **payout_request,
        "outcome": "approve",
        "reason": (
            "The carrier confirms a covered crew cancellation and the delay exceeds "
            "the plan's waiting period. The submitted expenses are supported by the "
            "receipts and fall within the benefit limit."
        ),
        "policy_section": "Section 3: Trip Delay",
    }
    messages: Iterator[AIMessage] = iter(
        [
            tool_message("get_claim_context", {"claim_id": claim_id}, "1"),
            tool_message("get_policy_wording", {}, "2"),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the story, policy and supporting documents.",
                },
                "3",
            ),
            tool_message("calculate_claim_payout", payout_request, "4"),
            tool_message("record_claim_decision", decision_request, "5"),
            tool_message(
                "pay_claim",
                {"claim_id": claim_id, "amount_inr": 34860.0},
                "6",
            ),
            AIMessage(
                content=(
                    "Approved for ₹34,860.00 under Section 3: Trip Delay. "
                    "The carrier notice and receipts support the claim."
                )
            ),
        ]
    )
    return StubModel(messages=messages)


def stub_over_limit_model(claim_id: str) -> StubModel:
    """Return a fake model that tries to pay an over-limit medical approval."""
    payout_request = {
        "claim_id": claim_id,
        "benefit": "emergency_medical",
        "items": [{"item_index": 0}],
        "paid_by_others_inr": 0,
    }
    decision_request = {
        **payout_request,
        "outcome": "approve",
        "reason": (
            "The medical report supports a sudden injury during the covered trip. "
            "The itemized bill supports the requested treatment under Section 7."
        ),
        "policy_section": "Section 7: Emergency Medical",
    }
    messages: Iterator[AIMessage] = iter(
        [
            tool_message("get_claim_context", {"claim_id": claim_id}, "1"),
            tool_message("get_policy_wording", {}, "2"),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the medical report and policy terms.",
                },
                "3",
            ),
            tool_message("calculate_claim_payout", payout_request, "4"),
            tool_message("record_claim_decision", decision_request, "5"),
            tool_message(
                "pay_claim",
                {"claim_id": claim_id, "amount_inr": 311250.0},
                "6",
            ),
            AIMessage(
                content=(
                    "Approved for ₹311,250.00 under Section 7: Emergency Medical. "
                    "Payment is waiting for handler sign-off."
                )
            ),
        ]
    )
    return StubModel(messages=messages)


def stub_refer_model(claim_id: str) -> StubModel:
    """Return a fake model that refers an unsupported baggage claim."""
    messages: Iterator[AIMessage] = iter(
        [
            tool_message("get_claim_context", {"claim_id": claim_id}, "1"),
            tool_message("get_policy_wording", {}, "2"),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the loss evidence and policy requirements.",
                },
                "3",
            ),
            tool_message(
                "record_claim_decision",
                {
                    "claim_id": claim_id,
                    "outcome": "refer",
                    "reason": (
                        "The required carrier report does not establish what happened. "
                        "A handler should review the evidence before deciding coverage."
                    ),
                    "policy_section": "Section 5: Baggage and Personal Effects",
                    "benefit": "baggage",
                    "items": [],
                    "paid_by_others_inr": 0,
                },
                "4",
            ),
            AIMessage(content="Referred for review under Section 5: Baggage."),
        ]
    )
    return StubModel(messages=messages)
