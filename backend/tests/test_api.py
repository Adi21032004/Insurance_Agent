from fastapi.testclient import TestClient

from agent.instructions import SYSTEM_PROMPT
from backend import database
from backend.main import app


def test_health(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_claims_returns_waiting_queue_in_inr(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        response = client.get("/api/claims")

    body = response.json()
    assert response.status_code == 200
    assert len(body["claims"]) == 25
    assert body["claims"][0]["claim_id"] == "CLM-1001"
    assert body["claims"][0]["amount_claimed_inr"] == 34860.0
    assert {claim["status"] for claim in body["claims"]} == {"waiting"}


def test_get_claim_has_full_context_in_inr(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        response = client.get("/api/claims/CLM-1001")

    body = response.json()
    assert response.status_code == 200
    assert body["claim"]["story"].startswith("My flight home from Rome")
    assert body["claim"]["items"][0]["amount_inr"] == 20335.0
    assert body["claim"]["items"][0].get("amount") is None
    assert body["policy"]["trip"]["trip_cost_inr"] == 257300.0
    assert body["customer"]["previous_claims"][0]["amount_paid_inr"] == 11620.0
    assert body["company"]["payout_approval_limit_inr"] == 83000.0
    assert "₹20,335.00" in body["policy_wording"] or "$" not in body["policy_wording"]


def test_instructions_are_the_agent_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        response = client.get("/api/instructions")

    assert response.status_code == 200
    assert response.json() == {"instructions": SYSTEM_PROMPT}


def test_unknown_claim_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    with TestClient(app) as client:
        response = client.get("/api/claims/CLM-9999")

    assert response.status_code == 404
