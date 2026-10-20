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
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "query": "trip delay waiting period expenses",
                },
                "2",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "section_id": "policy_wording:03",
                },
                "3",
            ),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "query": "trip delay accommodation meals receipts",
                },
                "4",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "section_id": "claims_manual:07",
                },
                "5",
            ),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the story, policy and supporting documents.",
                },
                "6",
            ),
            tool_message("calculate_claim_payout", payout_request, "7"),
            tool_message("record_claim_decision", decision_request, "8"),
            tool_message(
                "pay_claim",
                {"claim_id": claim_id, "amount_inr": 34860.0},
                "9",
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


def stub_queue_model(
    candidate_ids: list[str], max_claims: int, run_id: str
) -> StubModel:
    """Return a queue plan with a distinct order for capped-run tests."""
    selected = list(reversed(candidate_ids[:max_claims]))
    items = [
        {"claim_id": claim_id, "description": f"Review {claim_id}"}
        for claim_id in selected
    ]
    messages: Iterator[AIMessage] = iter(
        [
            tool_message(
                "save_run_plan",
                {"run_id": run_id, "items": items},
                "queue-1",
            ),
            AIMessage(content="Queue plan saved."),
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
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "query": "emergency medical benefit",
                },
                "2",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "section_id": "policy_wording:07",
                },
                "3",
            ),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "query": "medical records pre-existing condition",
                },
                "4",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "section_id": "claims_manual:07",
                },
                "5",
            ),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the medical report and policy terms.",
                },
                "6",
            ),
            tool_message("calculate_claim_payout", payout_request, "7"),
            tool_message("record_claim_decision", decision_request, "8"),
            tool_message(
                "pay_claim",
                {"claim_id": claim_id, "amount_inr": 311250.0},
                "9",
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


def stub_refer_model(claim_id: str, benefit: str = "baggage") -> StubModel:
    """Return a fake model that refers an unsupported baggage claim."""
    messages: Iterator[AIMessage] = iter(
        [
            tool_message("get_claim_context", {"claim_id": claim_id}, "1"),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "query": "baggage theft reporting evidence",
                },
                "2",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "policy_wording",
                    "section_id": "policy_wording:05",
                },
                "3",
            ),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "query": "missing carrier report required documents",
                },
                "4",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": claim_id,
                    "source": "claims_manual",
                    "section_id": "claims_manual:05",
                },
                "5",
            ),
            tool_message(
                "record_claim_progress",
                {
                    "claim_id": claim_id,
                    "step": "Review claim",
                    "detail": "Checking the loss evidence and policy requirements.",
                },
                "6",
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
                    "benefit": benefit,
                    "items": [],
                    "paid_by_others_inr": 0,
                },
                "7",
            ),
            AIMessage(content="Referred for review under Section 5: Baggage."),
        ]
    )
    return StubModel(messages=messages)
