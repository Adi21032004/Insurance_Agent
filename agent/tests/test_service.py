from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent import service


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(service.app) as test_client:
        yield test_client


def test_service_health_and_resume_route(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    resumed: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        service.api, "get_agent_claim", lambda claim_id: {"status": "in progress"}
    )
    monkeypatch.setattr(
        service,
        "resume_in_background",
        lambda claim_id, saver: resumed.append((claim_id, saver)),
    )

    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.post("/api/claims/CLM-1001/resume")

    assert response.status_code == 202
    assert response.json() == {"claim_id": "CLM-1001", "status": "accepted"}
    assert resumed == [("CLM-1001", service.app.state.checkpointer)]


def test_service_resume_rejects_missing_and_non_active_claims(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        service.api,
        "get_agent_claim",
        lambda claim_id: (_ for _ in ()).throw(KeyError(claim_id)),
    )
    missing = client.post("/api/claims/CLM-9999/resume")
    assert missing.status_code == 404

    monkeypatch.setattr(
        service.api, "get_agent_claim", lambda claim_id: {"status": "waiting"}
    )
    conflict = client.post("/api/claims/CLM-1001/resume")
    assert conflict.status_code == 409


def test_service_run_route_and_background_helpers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO")
    background_runner = service.run_in_background
    started: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        service.api,
        "get_run",
        lambda run_id: {"status": "in progress"},
    )
    monkeypatch.setattr(
        service,
        "run_in_background",
        lambda run_id, saver: started.append((run_id, saver)),
    )

    response = client.post("/api/runs/run-1/run")
    assert response.status_code == 202
    assert started == [("run-1", service.app.state.checkpointer)]

    monkeypatch.setattr(
        service.api, "get_agent_claim", lambda claim_id: {"status": "in progress"}
    )
    monkeypatch.setattr(service, "model", lambda claim_id: "model")
    monkeypatch.setattr(service, "build_claim_graph", lambda llm, saver: "graph")
    monkeypatch.setattr(service, "carry_on", lambda claim_id, graph: "resumed claim")
    service.resume_in_background("CLM-1001", "saver")

    monkeypatch.setattr(
        service, "process_saved_run", lambda run_id, saver: "completed run"
    )
    background_runner("run-1", "saver")
    assert "resumed claim" in caplog.text
    assert "completed run" in caplog.text


def test_service_run_route_reports_missing_and_non_active_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        service.api, "get_run", lambda run_id: (_ for _ in ()).throw(KeyError(run_id))
    )
    missing = client.post("/api/runs/missing/run")
    assert missing.status_code == 404

    monkeypatch.setattr(service.api, "get_run", lambda run_id: {"status": "done"})
    conflict = client.post("/api/runs/run-1/run")
    assert conflict.status_code == 409


def test_background_service_logs_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        service.api,
        "get_agent_claim",
        lambda claim_id: (_ for _ in ()).throw(OSError()),
    )
    service.resume_in_background("CLM-1001", "saver")

    monkeypatch.setattr(
        service,
        "process_saved_run",
        lambda run_id, saver: (_ for _ in ()).throw(RuntimeError("stopped")),
    )
    service.run_in_background("run-1", "saver")

    assert "Could not resume signed-off claim CLM-1001" in caplog.text
    assert "Agent run run-1 did not finish" in caplog.text
