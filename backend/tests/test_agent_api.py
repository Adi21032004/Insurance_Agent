from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import database, main


def test_start_claim_marks_in_progress_before_background_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    captured: list[tuple[str, str]] = []

    def finish_claim(claim_id: str, thread_id: str) -> str:
        captured.append((claim_id, thread_id))
        decision: dict[str, Any] = {
            "outcome": "refer",
            "reason": "The evidence is incomplete. A handler should review the claim.",
            "policy_section": "Section 11: How to Claim",
            "calculation": [],
            "payout_inr": 0.0,
            "needs_sign_off": False,
        }
        database.save_decision(claim_id, decision, "decided")
        return "Referred for review."

    monkeypatch.setattr(main, "run_agent", finish_claim)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    assert response.json() == {"status": "in progress"}
    assert captured[0][0] == "CLM-1001"
    assert captured[0][1].startswith("CLM-1001-")
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "decided"
    assert claim["decision"]["outcome"] == "refer"
    assert claim["progress"][0]["step"] == "Review started"


def test_start_claim_rejects_non_waiting_claim(client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 409
    assert response.json()["detail"] == "Claim is not waiting"


def test_failed_agent_run_returns_claim_to_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_run(claim_id: str, thread_id: str) -> str:
        raise RuntimeError("No model provider available")

    monkeypatch.setattr(main, "run_agent", fail_run)
    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "waiting"
    assert claim["progress"][-1]["step"] == "Review paused"


def test_agent_that_finishes_without_decision_returns_claim_to_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "run_agent", lambda claim_id, thread_id: "No decision.")

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 200
    assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"
