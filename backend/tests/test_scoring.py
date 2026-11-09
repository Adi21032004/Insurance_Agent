import pytest
from fastapi.testclient import TestClient

from backend.scoring import score_claim, score_evaluation


def test_score_claim_applies_wrongful_decline_approval_referral_and_amount_costs():
    cases = [
        ("decline", "approve", 0.0, 100.0, 1500.0),
        ("approve", "decline", 125.0, 0.0, 125.0),
        ("refer", "approve", 0.0, 100.0, 50.0),
        ("approve", "approve", 101.01, 100.0, 1.01),
        ("approve", "approve", 100.99, 100.0, 0.0),
    ]

    for actual, expected, actual_payout, expected_payout, cost in cases:
        result = score_claim(
            {
                "handler_decision": expected,
                "handler_payout": expected_payout,
                "decision": {"outcome": actual, "payout_inr": actual_payout},
            }
        )
        assert result["mistake_cost"] == pytest.approx(cost)


def test_undecided_claim_is_scored_as_a_referral():
    result = score_claim(
        {
            "handler_decision": "approve",
            "handler_payout": 100.0,
            "decision": None,
        }
    )

    assert result["agent_decision"] == "refer"
    assert result["mistake_cost"] == 50.0
    assert result["right"] is False


def test_evaluation_measures_include_accuracy_cost_and_per_claim_totals():
    result = score_evaluation(
        {
            "claims": [
                {
                    "handler_decision": "approve",
                    "handler_payout": 100.0,
                    "decision": {"outcome": "approve", "payout_inr": 100.0},
                    "seconds": 20.0,
                },
                {
                    "handler_decision": "decline",
                    "handler_payout": 0.0,
                    "decision": None,
                    "seconds": 40.0,
                },
            ],
            "model_cost": 0.02,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:01:00+00:00",
        }
    )

    assert result["measures"] == {
        "decision_accuracy": 0.5,
        "accuracy": 0.5,
        "cost_of_mistakes": 50.0,
        "straight_through": 0.5,
        "model_cost_per_claim": 0.01,
        "seconds_per_claim": 30.0,
        "run_seconds": 60.0,
    }


def test_evaluation_endpoint_scores_seeded_labels_and_unstarted_copies(
    client: TestClient,
):
    created = client.post(
        "/api/runs/evaluations",
        json={
            "run_id": "scored-evaluation",
            "evaluation_set": "held_out",
            "tiny": True,
            "agents": 5,
            "model": "model",
            "reasoning_effort": "low",
        },
    )
    response = client.get(f"/api/evaluations/{created.json()['run_id']}")

    assert response.status_code == 200
    result = response.json()
    assert len(result["claims"]) == 5
    assert all(claim["agent_decision"] == "refer" for claim in result["claims"])
    assert all(
        claim["right"] == (claim["handler_decision"] == "refer")
        for claim in result["claims"]
    )
    assert result["measures"]["model_cost_per_claim"] == 0.0
    assert result["measures"]["seconds_per_claim"] == 0.0
    assert result["measures"]["run_seconds"] is None
