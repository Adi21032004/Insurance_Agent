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
    assert resumed == [("CLM-1001", service.app.state.checkpoint_pool)]


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


def test_service_starts_a_reserved_claim_in_the_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    started: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        service.api,
        "get_agent_claim",
        lambda claim_id: {
            "status": "in progress",
            "decision": None,
            "thread_id": None,
        },
    )

    def start(claim_id: str, pool: Any):
        started.append((claim_id, pool))
        service.app.state.run_lock.release()

    monkeypatch.setattr(service, "claim_in_background", start)

    response = client.post("/api/claims/CLM-1001/run")

    assert response.status_code == 202
    assert response.json() == {"claim_id": "CLM-1001", "status": "accepted"}
    assert started == [("CLM-1001", service.app.state.checkpoint_pool)]


def test_resume_background_uses_a_claim_graph_and_logs_completion(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level("INFO")
    monkeypatch.setattr(
        service.api, "get_agent_claim", lambda claim_id: {"status": "in progress"}
    )
    monkeypatch.setattr(service, "claim_graphs", lambda pool: lambda claim_id: "graph")
    monkeypatch.setattr(service, "carry_on", lambda claim_id, graph: "resumed claim")
    service.app.state.resuming.add("CLM-1001")
    service.resume_in_background("CLM-1001", "pool")
    assert "resumed claim" in caplog.text
    assert "CLM-1001" not in service.app.state.resuming


def test_dispatch_start_persists_claims_runs_graph_and_releases_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    created: list[tuple[str, int, str, str]] = []
    selected: list[tuple[str, list[str]]] = []
    dispatched: list[dict[str, Any]] = []

    def create_dispatch(
        run_id: str, agents: int, model: str, reasoning_effort: str
    ) -> dict[str, Any]:
        created.append((run_id, agents, model, reasoning_effort))
        return {"run_id": run_id, "agents": agents}

    monkeypatch.setattr(service.api, "create_dispatch", create_dispatch)
    monkeypatch.setattr(
        service.api,
        "get_dispatch_claims",
        lambda: [
            {"claim_id": "CLM-1001", "status": "waiting"},
            {"claim_id": "CLM-1002", "status": "in progress"},
            {"claim_id": "CLM-1003", "status": "decided"},
        ],
    )
    monkeypatch.setattr(
        service.api,
        "record_run_claims",
        lambda run_id, claim_ids: selected.append((run_id, claim_ids)),
    )
    monkeypatch.setattr(
        service.dispatcher,
        "work_run",
        lambda run, work, resuming: dispatched.append(run) or [],
    )

    response = client.post("/api/dispatches", json={"agents": 2})

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "agents": 2}
    assert created[0][1:] == (2, "openai/gpt-6-luna", "low")
    assert selected == [(created[0][0], ["CLM-1001", "CLM-1002"])]
    assert dispatched[0]["claim_ids"] == ["CLM-1001", "CLM-1002"]
    assert dispatched[0]["agents"] == 2
    assert not service.app.state.run_lock.locked()


def test_dispatch_start_rejects_an_active_run(client: TestClient):
    service.app.state.run_lock.acquire()
    try:
        response = client.post("/api/dispatches", json={"agents": 2})
    finally:
        service.app.state.run_lock.release()

    assert response.status_code == 409
    assert response.json()["detail"] == "A dispatch or an evaluation is already running"


def test_evaluation_start_creates_copies_and_runs_the_dispatcher(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    created: list[tuple[Any, ...]] = []
    dispatched: list[dict[str, Any]] = []

    def create_evaluation(  # noqa: PLR0913, PLR0917
        run_id: str,
        evaluation_set: str,
        tiny: bool,
        agents: int,
        model_name: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        created.append(
            (run_id, evaluation_set, tiny, agents, model_name, reasoning_effort)
        )
        return {"run_id": run_id, "claim_ids": ["CLM-1104-E1"]}

    monkeypatch.setattr(service.api, "create_evaluation", create_evaluation)
    monkeypatch.setattr(
        service.dispatcher,
        "work_run",
        lambda run, work: dispatched.append(run) or [],
    )

    response = client.post(
        "/api/evaluations",
        json={"evaluation_set": "held_out", "tiny": True, "agents": 5},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "evaluation_set": "held_out",
        "tiny": True,
        "agents": 5,
    }
    assert created[0][1:] == (
        "held_out",
        True,
        5,
        "openai/gpt-6-luna",
        "low",
    )
    assert dispatched[0]["claim_ids"] == ["CLM-1104-E1"]
    assert not service.app.state.run_lock.locked()


def test_background_service_logs_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        service.api,
        "get_agent_claim",
        lambda claim_id: (_ for _ in ()).throw(OSError()),
    )
    service.resume_in_background("CLM-1001", "saver")

    assert "Could not resume signed-off claim CLM-1001" in caplog.text
