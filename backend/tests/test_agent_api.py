from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import database, main


def test_start_claim_marks_in_progress_before_background_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    captured: list[tuple[str, str, str]] = []

    def finish_claim(run_id: str, thread_id: str) -> str:
        run = database.get_run(run_id)
        assert run is not None
        assert len(run["todos"]) == 1
        claim_id = run["todos"][0]["claim_id"]
        captured.append((claim_id, run_id, thread_id))
        decision: dict[str, Any] = {
            "outcome": "refer",
            "reason": "The evidence is incomplete. A handler should review the claim.",
            "policy_section": "Section 11: How to Claim",
            "calculation": [],
            "payout_inr": 0.0,
            "needs_sign_off": False,
        }
        database.save_decision(claim_id, decision, "decided")
        database.set_todo_status(run["todos"][0]["todo_id"], "done")
        database.finish_run(run_id, "Reviewed one claim.")
        return "Referred for review."

    monkeypatch.setattr(main, "run_queue_agent", finish_claim)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    assert response.json()["status"] == "in progress"
    assert response.json()["run_id"] == captured[0][1]
    assert captured[0][0] == "CLM-1001"
    assert captured[0][2].startswith(captured[0][1])
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "decided"
    assert claim["decision"]["outcome"] == "refer"
    assert claim["progress"][0]["step"] == "Review started"


def test_start_claim_rejects_non_waiting_claim(client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Claim is not waiting"


def test_worker_stopping_before_persisting_failure_keeps_claim_recoverable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_run(run_id: str, thread_id: str) -> str:
        raise RuntimeError("No model provider available")

    monkeypatch.setattr(main, "run_queue_agent", fail_run)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "in progress"
    assert claim["failure_count"] == 0
    assert claim["decision"] is None


def test_worker_stopping_without_a_decision_keeps_claim_recoverable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def no_decision(run_id: str, thread_id: str) -> str:
        raise RuntimeError("No decision.")

    monkeypatch.setattr(main, "run_queue_agent", no_decision)

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "in progress"
    assert claim["failure_count"] == 0
    assert claim["decision"] is None


def test_decision_and_payment_api_replays_are_idempotent(client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")
    request = {
        "outcome": "decline",
        "reason": "The evidence does not meet the policy requirements.",
        "policy_section": "Section 9: General Exclusions",
        "benefit": "trip_delay",
        "items": [],
        "paid_by_others_inr": 0,
    }

    first_decision = client.post("/api/claims/CLM-1001/decision", json=request)
    replayed_decision = client.post("/api/claims/CLM-1001/decision", json=request)
    database.save_decision(
        "CLM-1002",
        {
            "outcome": "approve",
            "payout_inr": 0.0,
            "needs_sign_off": False,
            "calculation": [],
        },
        "decided",
    )
    database.save_decision(
        "CLM-1003",
        {
            "outcome": "approve",
            "payout_inr": 100000.0,
            "needs_sign_off": True,
            "calculation": [],
        },
        "waiting for sign-off",
    )
    first_payment = client.post("/api/claims/CLM-1002/pay", json={"amount_inr": 0})
    replayed_payment = client.post("/api/claims/CLM-1002/pay", json={"amount_inr": 0})
    first_signoff = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    replayed_signoff = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    first_over_limit_payment = client.post(
        "/api/claims/CLM-1003/pay", json={"amount_inr": 100000}
    )
    replayed_over_limit_payment = client.post(
        "/api/claims/CLM-1003/pay", json={"amount_inr": 100000}
    )

    assert first_decision.status_code == 200
    assert replayed_decision.status_code == 200
    assert replayed_decision.json() == first_decision.json()
    assert first_payment.status_code == 200
    assert replayed_payment.status_code == 200
    assert replayed_payment.json() == first_payment.json()
    assert first_signoff.status_code == 200
    assert replayed_signoff.status_code == 200
    assert replayed_signoff.json() == first_signoff.json()
    assert first_over_limit_payment.status_code == 200
    assert replayed_over_limit_payment.status_code == 200
    assert replayed_over_limit_payment.json() == first_over_limit_payment.json()
    with database.connection() as db:
        decision_row = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1001'"
        ).fetchone()
        payment_row = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1002'"
        ).fetchone()
        signoff_row = db.execute(
            "SELECT COUNT(*) FROM signoffs WHERE claim_id = 'CLM-1003'"
        ).fetchone()
        over_limit_payment_row = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1003'"
        ).fetchone()
    assert decision_row is not None
    assert payment_row is not None
    assert signoff_row is not None
    assert over_limit_payment_row is not None
    decisions = decision_row[0]
    payments = payment_row[0]
    signoffs = signoff_row[0]
    over_limit_payments = over_limit_payment_row[0]
    assert decisions == 1
    assert payments == 1
    assert signoffs == 1
    assert over_limit_payments == 1
