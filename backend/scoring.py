"""Evaluation measures derived from agent decisions and seeded answer keys."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

WRONGFUL_DECLINE = 1500.0
NEEDLESS_REFERRAL = 50.0


def score_claim(claim: dict[str, Any]) -> dict[str, Any]:
    """Compare one agent decision with its handler's labelled decision."""
    expected = claim["handler_decision"]
    expected_payout = float(claim["handler_payout"])
    decision = claim["decision"]
    actual = decision["outcome"] if decision is not None else "refer"
    actual_payout = (
        float(decision.get("payout_inr", 0.0)) if decision is not None else 0.0
    )
    decision_right = actual == expected
    amount_right = actual != "approve" or abs(actual_payout - expected_payout) <= 1.0
    if expected == "approve" and actual == "decline":
        mistake_cost = WRONGFUL_DECLINE
    elif actual == "approve" and expected != "approve":
        mistake_cost = actual_payout
    elif actual == "refer" and expected != "refer":
        mistake_cost = NEEDLESS_REFERRAL
    elif expected == "approve" and actual == "approve" and not amount_right:
        mistake_cost = abs(actual_payout - expected_payout)
    else:
        mistake_cost = 0.0
    return {
        **claim,
        "agent_decision": actual,
        "agent_payout": actual_payout,
        "right": decision_right and amount_right,
        "decision_right": decision_right,
        "mistake_cost": mistake_cost,
    }


def score_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Add per-claim scores and aggregate evaluation measures."""
    claims = [score_claim(claim) for claim in evaluation["claims"]]
    count = len(claims)
    if count == 0:
        raise ValueError("An evaluation must contain at least one claim")
    measures = {
        "decision_accuracy": sum(claim["decision_right"] for claim in claims) / count,
        "accuracy": sum(claim["right"] for claim in claims) / count,
        "cost_of_mistakes": sum(claim["mistake_cost"] for claim in claims),
        "straight_through": sum(claim["agent_decision"] != "refer" for claim in claims)
        / count,
        "model_cost_per_claim": evaluation["model_cost"] / count,
        "seconds_per_claim": sum(claim["seconds"] or 0 for claim in claims) / count,
        "run_seconds": None,
    }
    if evaluation["finished_at"] is not None:
        measures["run_seconds"] = max(
            0.0,
            (
                _timestamp(evaluation["finished_at"])
                - _timestamp(evaluation["created_at"])
            ).total_seconds(),
        )
    return {**evaluation, "claims": claims, "measures": measures}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
