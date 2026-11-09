import pytest
from fastapi.testclient import TestClient

from backend import database, main


def test_start_claim_marks_in_progress_before_background_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    started: list[str] = []
    monkeypatch.setattr(main.agent_service, "start_claim", started.append)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    assert response.json()["status"] == "in progress"
    assert started == ["CLM-1001"]
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "in progress"
    assert claim["decision"] is None
    assert claim["progress"][0]["step"] == "Review started"


def test_start_claim_rejects_non_waiting_claim(client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Claim is not waiting"


def test_unavailable_agent_service_restores_claim_to_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_start(claim_id: str):
        raise RuntimeError("The agent's service isn't running")

    monkeypatch.setattr(main.agent_service, "start_claim", fail_start)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 503
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "waiting"
    assert claim["failure_count"] == 0
    assert claim["decision"] is None


def test_agent_service_conflict_restores_claim_to_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def reject_start(claim_id: str):
        raise main.agent_service.AgentServiceConflict("A dispatch is already running")

    monkeypatch.setattr(main.agent_service, "start_claim", reject_start)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 409
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "waiting"


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


def test_model_usage_and_work_time_are_recorded_on_the_dispatch(
    client: TestClient,
):
    run_id = "metric-dispatch"
    database.create_dispatch(run_id, 1, "model", "low")
    database.record_run_claims(run_id, ["CLM-1001"])

    model_response = client.post(
        "/api/claims/CLM-1001/model-calls",
        json={
            "run_id": run_id,
            "input_tokens": 1200,
            "output_tokens": 300,
            "cost": 0.0042,
        },
    )
    time_response = client.post(
        "/api/claims/CLM-1001/work-times",
        json={"run_id": run_id, "seconds": 12.5},
    )
    run = database.get_run(run_id)
    assert run is not None

    assert model_response.status_code == 200
    assert time_response.status_code == 200
    assert run["claims"][0]["model_cost"] == pytest.approx(0.0042)
    assert run["claims"][0]["seconds"] == pytest.approx(12.5)


def test_model_usage_and_work_time_reject_unknown_or_unstarted_records(
    client: TestClient,
):
    payload = {
        "run_id": None,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost": 0.01,
    }

    assert (
        client.post("/api/claims/CLM-9999/model-calls", json=payload).status_code == 404
    )
    assert (
        client.post("/api/claims/CLM-1001/model-calls", json=payload).status_code == 409
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/work-times",
            json={"run_id": None, "seconds": -1},
        ).status_code
        == 422
    )
