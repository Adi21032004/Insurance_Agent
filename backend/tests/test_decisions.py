from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import database, main
from backend.money import to_inr
from backend.payout import item_quantity


def start_claim(client: TestClient, claim_id: str) -> None:
    database.set_claim_status(claim_id, "in progress")


def request_body(outcome: str = "approve") -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason": "The evidence supports this outcome under the policy.",
        "policy_section": "Section 3: Trip Delay",
        "benefit": "trip_delay",
        "items": [
            {"item_index": 0},
            {"item_index": 1},
            {"item_index": 2},
        ],
        "paid_by_others_inr": 0,
    }


def test_payout_calculation_converts_items_and_applies_plan_limit(client):
    response = client.post(
        "/api/agent/claims/CLM-1001/payout",
        json={
            "benefit": "trip_delay",
            "items": [
                {"item_index": 0},
                {"item_index": 1},
                {"item_index": 2},
            ],
            "paid_by_others_inr": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["payout_inr"] == 34860.0
    assert response.json()["needs_sign_off"] is False
    assert sum(step["amount_inr"] for step in response.json()["calculation"]) == 34860.0


def test_medical_calculation_applies_deductible_and_signoff(client):
    response = client.post(
        "/api/agent/claims/CLM-1003/payout",
        json={
            "benefit": "emergency_medical",
            "items": [{"item_index": 0}],
            "paid_by_others_inr": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["payout_inr"] == 311250.0
    assert response.json()["needs_sign_off"] is True
    assert response.json()["calculation"][-1]["amount_inr"] == -4150.0


def test_baggage_calculation_applies_per_item_cap_and_deductible(client):
    response = client.post(
        "/api/agent/claims/CLM-1002/payout",
        json={
            "benefit": "baggage",
            "items": [{"item_index": 1}],
            "paid_by_others_inr": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["calculation"][0]["amount_inr"] == 24900.0
    assert response.json()["payout_inr"] == 20750.0


def test_money_rounding_and_description_quantity():
    assert to_inr("0.105", 83) == 8.72
    assert item_quantity("Four shirts") == 4
    assert item_quantity("Camera, one item") == 1


def test_calculation_rejects_wrong_benefit_and_duplicate_item(client):
    wrong_benefit = client.post(
        "/api/agent/claims/CLM-1001/payout",
        json={"benefit": "baggage", "items": [], "paid_by_others_inr": 0},
    )
    duplicate_item = client.post(
        "/api/agent/claims/CLM-1001/payout",
        json={
            "benefit": "trip_delay",
            "items": [
                {"item_index": 0},
                {"item_index": 0},
            ],
            "paid_by_others_inr": 0,
        },
    )

    assert wrong_benefit.status_code == 422
    assert duplicate_item.status_code == 422


def test_agent_progress_is_persisted_only_during_a_run(client):
    waiting = client.post(
        "/api/claims/CLM-1001/progress",
        json={"step": "Review", "detail": "Reading the claim."},
    )
    start_claim(client, "CLM-1001")
    recorded = client.post(
        "/api/claims/CLM-1001/progress",
        json={"step": "Review", "detail": "Reading the claim."},
    )

    assert waiting.status_code == 409
    assert recorded.status_code == 200
    assert client.get("/api/claims/CLM-1001").json()["progress"][0]["step"] == "Review"


def test_decline_and_refer_decisions_have_zero_payout(client):
    start_claim(client, "CLM-1001")
    declined = client.post(
        "/api/claims/CLM-1001/decision", json=request_body("decline")
    )
    start_claim(client, "CLM-1002")
    referred_body = request_body("refer")
    referred_body.update(
        {
            "benefit": "baggage",
            "items": [{"item_index": 0}],
            "policy_section": "Section 5: Baggage",
        }
    )
    referred = client.post("/api/claims/CLM-1002/decision", json=referred_body)

    assert declined.status_code == 200
    assert declined.json()["decision"]["payout_inr"] == 0
    assert declined.json()["status"] == "decided"
    assert referred.status_code == 200
    assert referred.json()["decision"]["payout_inr"] == 0


def test_exact_approval_limit_does_not_require_signoff(
    client, monkeypatch: pytest.MonkeyPatch
):
    def exact_limit(
        claim_id: str,
        benefit: str,
        items: list[dict[str, Any]],
        paid_by_others_inr: float,
    ) -> dict[str, Any]:
        return {"calculation": [], "payout_inr": 83000.0, "needs_sign_off": False}

    monkeypatch.setattr(main, "calculate", exact_limit)
    start_claim(client, "CLM-1003")
    body = request_body()
    body.update(
        {
            "benefit": "emergency_medical",
            "items": [{"item_index": 0}],
            "policy_section": "Section 7: Emergency Medical",
        }
    )
    response = client.post("/api/claims/CLM-1003/decision", json=body)

    assert response.status_code == 200
    assert response.json()["status"] == "decided"
    assert response.json()["decision"]["needs_sign_off"] is False
