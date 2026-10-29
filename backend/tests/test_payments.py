from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import database, main


def request_body() -> dict[str, Any]:
    return {
        "outcome": "approve",
        "reason": "The evidence supports this outcome under the policy.",
        "policy_section": "Section 7: Emergency Medical",
        "benefit": "emergency_medical",
        "items": [{"item_index": 0}],
        "paid_by_others_inr": 0,
    }


def start_claim(client: TestClient, claim_id: str) -> None:
    database.set_claim_status(claim_id, "in progress")


def over_limit(
    claim_id: str,
    benefit: str,
    items: list[dict[str, Any]],
    paid_by_others_inr: float,
) -> dict[str, Any]:
    return {"calculation": [], "payout_inr": 83000.01, "needs_sign_off": True}


def test_over_limit_payment_is_blocked_until_handler_signoff(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "calculate", over_limit)
    start_claim(client, "CLM-1003")
    approved = client.post("/api/claims/CLM-1003/decision", json=request_body())
    blocked = client.post("/api/claims/CLM-1003/pay", json={"amount_inr": 83000.01})

    assert approved.json()["status"] == "waiting for sign-off"
    assert blocked.status_code == 403
    assert (
        blocked.json()["detail"] == "A payout over $1,000.00 needs a handler's sign-off"
    )
    assert client.get("/api/claims/CLM-1003").json()["payment"] is None

    signed_off = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    paid = client.post("/api/claims/CLM-1003/pay", json={"amount_inr": 83000.01})

    assert signed_off.status_code == 200
    assert signed_off.json()["status"] == "in progress"
    assert paid.status_code == 200
    assert paid.json() == {
        "status": "paid",
        "simulated": True,
        "amount_inr": 83000.01,
    }
    claim = client.get("/api/claims/CLM-1003").json()
    assert claim["payment"] == {
        "amount_inr": 83000.01,
        "simulated": True,
        "signed_off_by": "Dana Whitfield",
        "signed_off_at": claim["sign_off_at"],
    }
    assert claim["sign_off_handler_id"] == "HND-01"


def test_exact_limit_payment_succeeds_without_signoff(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
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
    client.post("/api/claims/CLM-1003/decision", json=request_body())
    paid = client.post("/api/claims/CLM-1003/pay", json={"amount_inr": 83000.0})

    assert paid.status_code == 200
    assert paid.json()["amount_inr"] == 83000.0


def test_payment_rejects_mismatch_and_unapproved_payment_and_replays_duplicate(
    client: TestClient,
):
    unapproved = client.post("/api/claims/CLM-1001/pay", json={"amount_inr": 1})
    start_claim(client, "CLM-1001")
    low_limit_body = {
        "outcome": "approve",
        "reason": "The evidence supports this outcome under the policy.",
        "policy_section": "Section 3: Trip Delay",
        "benefit": "trip_delay",
        "items": [{"item_index": 0}, {"item_index": 1}, {"item_index": 2}],
        "paid_by_others_inr": 0,
    }
    approved = client.post("/api/claims/CLM-1001/decision", json=low_limit_body)
    mismatch = client.post("/api/claims/CLM-1001/pay", json={"amount_inr": 1})
    payout = approved.json()["decision"]["payout_inr"]
    paid = client.post("/api/claims/CLM-1001/pay", json={"amount_inr": payout})
    duplicate = client.post("/api/claims/CLM-1001/pay", json={"amount_inr": payout})

    assert unapproved.status_code == 409
    assert mismatch.status_code == 422
    assert paid.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == paid.json()


def test_only_a_known_handler_can_sign_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "calculate", over_limit)
    start_claim(client, "CLM-1003")
    client.post("/api/claims/CLM-1003/decision", json=request_body())
    invalid_handler = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-99"}
    )
    valid_handler = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-02"}
    )

    assert invalid_handler.status_code == 404
    assert valid_handler.status_code == 200
