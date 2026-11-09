import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, inspect
from sqlalchemy.pool import NullPool, QueuePool

from backend import database, dependencies, lookups, main, runs, views
from backend.main import app


def test_database_uses_normalized_core_tables_and_json_columns(client: TestClient):
    table_names = set(inspect(database.engine()).get_table_names())
    settings = database.get_setting("handlers")

    assert {
        "settings",
        "documents",
        "handlers",
        "customers",
        "policies",
        "claims",
        "decisions",
        "payments",
        "steps",
        "runs",
        "todos",
    }.issubset(table_names)
    assert "events" not in table_names
    assert isinstance(database.policies_table.c.policy_json.type, JSON)
    assert isinstance(database.customers_table.c.customer_json.type, JSON)
    assert settings[0]["handler_id"] == "HND-01"


def test_database_selects_postgres_or_sqlite_without_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/undercover")
    postgres = database.engine()

    assert postgres.url.drivername == "postgresql+psycopg"
    assert isinstance(postgres.pool, QueuePool)
    assert postgres.pool.size() == 4
    assert postgres.pool._max_overflow == 0
    assert database.POSTGRES_CONNECT_ARGS == {"options": "-c search_path=undercover"}
    assert postgres.pool.checkedout() == 0

    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "local.sqlite3")
    sqlite = database.engine()

    assert sqlite.url.drivername == "sqlite"
    assert isinstance(sqlite.pool, NullPool)


def required_run(run_id: str) -> dict[str, Any]:
    """Return a persisted run or fail the test with a clear assertion."""
    run = database.get_run(run_id)
    assert run is not None
    return run


def decision(reason: str = "A handler should review the claim.") -> dict[str, object]:
    return {
        "outcome": "refer",
        "reason": reason,
        "policy_section": "Section 3: Trip Delay",
        "calculation": [],
        "payout_inr": 0.0,
        "needs_sign_off": False,
    }


def finish_stub_queue(run_id: str, thread_id: str) -> str:
    run = database.get_run(run_id)
    assert run is not None
    if run["run_type"] == "queue":
        waiting = database.list_waiting_claim_ids(run["max_claims"])
        database.select_run_claims(run_id, waiting)
    run = database.get_run(run_id)
    assert run is not None
    for todo in run["todos"]:
        database.set_todo_status(todo["todo_id"], "in progress")
        database.save_decision(todo["claim_id"], decision(), "decided")
        database.set_todo_status(todo["todo_id"], "done")
    summary = f"Reviewed {len(run['todos'])} claims."
    database.finish_run(run_id, summary)
    return summary


def test_queue_run_is_capped_and_persists_ordered_todos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")

    monkeypatch.setattr(main, "run_queue_agent", finish_stub_queue)
    with TestClient(app) as client:
        response = client.post("/api/agent/runs", json={"max_claims": 5})
        run_id = response.json()["run_id"]
        run = client.get(f"/api/agent/runs/{run_id}").json()
        queue = client.get("/api/claims").json()["claims"]
        active = client.get("/api/agent/runs/current").json()

    assert response.status_code == 200
    assert response.json()["status"] == "in progress"
    assert response.json()["max_claims"] == 5
    assert run["status"] == "completed"
    assert len(run["todos"]) == 5
    assert [item["position"] for item in run["todos"]] == list(range(5))
    assert {item["status"] for item in run["todos"]} == {"done"}
    assert sum(claim["status"] == "waiting" for claim in queue) == 20
    assert active == {"run": None}


def test_current_run_is_null_when_no_run_is_active(client: TestClient):
    response = client.get("/api/agent/runs/current")

    assert response.status_code == 200
    assert response.json() == {"run": None}


def test_dispatch_claim_selection_reserves_claims_and_preserves_order(
    client: TestClient,
):
    database.create_dispatch("dispatch-selection", 2, "model", "low")

    response = client.put(
        "/api/runs/dispatch-selection/claims",
        json={"claim_ids": ["CLM-1002", "CLM-1001"]},
    )
    run = database.get_run("dispatch-selection")
    queue = {
        claim["claim_id"]: claim["status"]
        for claim in client.get("/api/claims").json()["claims"]
    }

    assert response.status_code == 200
    assert run is not None
    assert run["claim_ids"] == ["CLM-1002", "CLM-1001"]
    assert queue["CLM-1002"] == "in progress"
    assert queue["CLM-1001"] == "in progress"


def test_dispatch_claim_selection_rejects_unknown_claim_without_partial_updates(
    client: TestClient,
):
    database.create_dispatch("dispatch-invalid-selection", 2, "model", "low")

    response = client.put(
        "/api/runs/dispatch-invalid-selection/claims",
        json={"claim_ids": ["CLM-1001", "CLM-9999"]},
    )
    queue = {
        claim["claim_id"]: claim["status"]
        for claim in client.get("/api/claims").json()["claims"]
    }

    assert response.status_code == 404
    assert queue["CLM-1001"] == "waiting"
    assert required_run("dispatch-invalid-selection")["claim_ids"] == []


def test_dispatch_finish_builds_summary_and_cost_time_totals_from_records(
    client: TestClient,
):
    run_id = "dispatch-summary"
    database.create_dispatch(run_id, 1, "model", "low")
    database.record_run_claims(run_id, ["CLM-1001"])
    database.record_model_call("CLM-1001", 10, 4, 0.125, run_id)
    database.record_work_time("CLM-1001", 0.75, run_id)

    summary = database.finish_run_summary(run_id, "caller summary")
    run = required_run(run_id)

    assert summary == {
        "approved": 0,
        "declined": 0,
        "referred": 0,
        "waiting_for_sign_off": [],
        "left_for_handler": [],
        "unfinished": ["CLM-1001"],
    }
    assert run["status"] == "finished"
    assert run["run_seconds"] is not None
    assert run["run_seconds"] >= 0
    assert run["model_cost"] == pytest.approx(0.125)
    assert run["seconds"] == pytest.approx(0.75)
    assert run["claims"][0]["model_cost"] == pytest.approx(0.125)
    assert run["claims"][0]["seconds"] == pytest.approx(0.75)


def test_evaluation_copies_seeded_claims_without_changing_the_queue(
    client: TestClient,
):
    assert database.evaluation_seed_counts() == (25, 50)
    queue_before = [
        claim["claim_id"] for claim in client.get("/api/claims").json()["claims"]
    ]
    response = client.post(
        "/api/runs/evaluations",
        json={
            "run_id": "evaluation-copies",
            "evaluation_set": "held_out",
            "tiny": True,
            "agents": 5,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    evaluation = database.get_evaluation(run_id)
    queue_after = [
        claim["claim_id"] for claim in client.get("/api/claims").json()["claims"]
    ]

    assert evaluation is not None
    assert queue_after == queue_before
    assert [claim["claim_id"] for claim in evaluation["claims"]] == [
        "CLM-1104-E1",
        "CLM-1106-E1",
        "CLM-1110-E1",
        "CLM-1112-E1",
        "CLM-1114-E1",
    ]
    assert all(claim["evaluation_id"] == run_id for claim in evaluation["claims"])
    assert all(claim["handler_decision"] for claim in evaluation["claims"])
    copy = database.get_claim_record("CLM-1104-E1")
    assert copy is not None
    assert copy["copy_of"] == "CLM-1104"
    assert "handler_decision" not in copy
    assert "reason" not in copy
    assert "CLM-1104-E1" not in queue_after
    copy_detail = client.get("/api/claims/CLM-1104-E1").json()
    agent_copy = client.get("/api/agent/claims/CLM-1104-E1").json()
    assert copy_detail["evaluation_id"] == run_id
    assert copy_detail["copy_of"] == "CLM-1104"
    assert "handler_decision" not in agent_copy


def test_evaluation_copy_cannot_be_signed_off(client: TestClient):
    database.create_evaluation(
        "evaluation-signoff",
        "held_out",
        True,
        5,
        "model",
        "low",
    )
    database.set_claim_status("CLM-1104-E1", "in progress")
    database.save_decision(
        "CLM-1104-E1",
        {
            "outcome": "approve",
            "reason": "Needs handler approval.",
            "policy_section": "Section 3",
            "calculation": [],
            "payout_inr": 100000.0,
            "needs_sign_off": True,
        },
        "waiting for sign-off",
    )

    response = client.post(
        "/api/claims/CLM-1104-E1/sign-off",
        json={"handler_id": "HND-01"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Claim CLM-1104-E1 is a copy in an evaluation and can't be signed off"
    )


def test_unknown_evaluation_set_is_rejected(client: TestClient):
    response = client.post(
        "/api/runs/evaluations",
        json={
            "run_id": "invalid-evaluation",
            "evaluation_set": "unknown",
            "tiny": True,
            "agents": 3,
            "model": "model",
            "reasoning_effort": "low",
        },
    )

    assert response.status_code == 422


def test_only_one_run_can_be_active_and_conflict_returns_run_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "process_queue_run", lambda run_id: None)

    first = client.post("/api/agent/runs", json={"max_claims": 2})
    selected = database.list_waiting_claim_ids(2)
    client.post(
        f"/api/agent/runs/{first.json()['run_id']}/select",
        json={"claim_ids": selected},
    )
    active = client.get("/api/agent/runs/current").json()["run"]
    second = client.post("/api/agent/runs", json={"max_claims": 1})

    assert first.status_code == 200
    assert active["run_id"] == first.json()["run_id"]
    assert len(active["todos"]) == 2
    assert second.status_code == 409
    assert second.json()["detail"]["run_id"] == first.json()["run_id"]
    assert (
        sum(
            claim["status"] == "in progress"
            for claim in client.get("/api/claims").json()["claims"]
        )
        == 2
    )


@pytest.mark.parametrize("max_claims", [0, -1, 26, "five", 1.5])
def test_queue_run_rejects_invalid_caps(client: TestClient, max_claims: object):
    response = client.post("/api/agent/runs", json={"max_claims": max_claims})

    assert response.status_code == 422
    assert client.get("/api/agent/runs/current").json() == {"run": None}


def test_selection_rejects_invalid_sets_without_partial_state(client: TestClient):
    run_id = "selection-run"
    database.create_run(run_id, "queue", 1)

    too_many = client.post(
        f"/api/agent/runs/{run_id}/select", json={"claim_ids": ["CLM-1001", "CLM-1002"]}
    )
    duplicate = client.post(
        f"/api/agent/runs/{run_id}/select", json={"claim_ids": ["CLM-1001", "CLM-1001"]}
    )
    database.set_claim_status("CLM-1002", "decided")
    not_waiting = client.post(
        f"/api/agent/runs/{run_id}/select", json={"claim_ids": ["CLM-1002"]}
    )
    queue = client.get("/api/claims").json()["claims"]
    run = client.get(f"/api/agent/runs/{run_id}").json()

    assert too_many.status_code == 422
    assert duplicate.status_code == 422
    assert not_waiting.status_code == 409
    assert {claim["claim_id"]: claim["status"] for claim in queue}[
        "CLM-1001"
    ] == "waiting"
    assert run["todos"] == []


def test_simultaneous_acquisitions_have_one_winner(client: TestClient):
    run_id = "concurrent-run"
    database.create_run(run_id, "queue", 1)
    barrier = Barrier(2)

    def acquire() -> bool:
        barrier.wait()
        try:
            database.select_run_claims(run_id, ["CLM-1001"])
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: acquire(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1
    claim = database.get_claim_record("CLM-1001")
    assert claim is not None
    assert claim["status"] == "in progress"
    assert claim["run_id"] == run_id
    assert len(required_run(run_id)["todos"]) == 1


def test_failed_attempt_retries_in_run_and_failure_count_carries_across_runs(
    client: TestClient,
):
    first_run = "first-failure-run"
    database.create_run(first_run, "claim", 1)
    database.select_run_claims(first_run, ["CLM-1001"])
    first_todo = required_run(first_run)["todos"][0]
    database.set_todo_status(first_todo["todo_id"], "in progress")

    first_failure = database.fail_claim_attempt(
        first_run, "CLM-1001", "Provider timeout"
    )
    first_attempt = database.get_claim_record("CLM-1001")
    retry_number = database.retry_claim_attempt(first_run, "CLM-1001")
    retried = required_run(first_run)["todos"][0]

    assert first_failure == {
        "failure_count": 1,
        "retry": True,
        "claim_status": "waiting",
        "todo_status": "to do",
    }
    assert first_attempt is not None
    assert first_attempt["status"] == "waiting"
    assert retry_number == 2
    assert retried["attempt_count"] == 2

    database.fail_claim_attempt(first_run, "CLM-1001", "Tool failed again")
    failed = database.get_claim_record("CLM-1001")
    failed_todo = required_run(first_run)["todos"][0]

    assert failed is not None
    assert failed["status"] == "needs handler"
    assert failed["failure_count"] == 2
    assert failed_todo["status"] == "left for handler"


def test_failure_count_from_an_interrupted_run_applies_to_next_run(
    client: TestClient,
):
    first_run = "cross-run-first"
    database.create_run(first_run, "claim", 1)
    database.select_run_claims(first_run, ["CLM-1001"])
    first_todo = required_run(first_run)["todos"][0]
    database.set_todo_status(first_todo["todo_id"], "in progress")
    database.fail_claim_attempt(first_run, "CLM-1001", "Provider timeout")
    database.interrupt_run(first_run)

    second_run = "cross-run-second"
    database.create_run(second_run, "queue", 1)
    database.select_run_claims(second_run, ["CLM-1001"])
    second_todo = required_run(second_run)["todos"][0]
    database.set_todo_status(second_todo["todo_id"], "in progress")
    result = database.fail_claim_attempt(second_run, "CLM-1001", "Tool failed")
    claim = database.get_claim_record("CLM-1001")

    assert result["failure_count"] == 2
    assert result["retry"] is False
    assert claim is not None
    assert claim["failure_count"] == 2
    assert claim["status"] == "needs handler"
    assert "CLM-1001" not in database.list_waiting_claim_ids(25)


def test_expired_run_is_interrupted_and_creates_a_recovery_run(
    client: TestClient,
):
    run_id = "crashed-run"
    database.create_run(run_id, "queue", 1)
    database.select_run_claims(run_id, ["CLM-1001"])
    todo = required_run(run_id)["todos"][0]
    database.set_todo_status(todo["todo_id"], "in progress")
    database.set_claim_thread_id("CLM-1001", "saved-claim-thread")
    with database.connection() as db:
        db.execute(
            "UPDATE runs SET heartbeat_at = '2000-01-01 00:00:00' WHERE run_id = ?",
            (run_id,),
        )
        db.execute(
            "UPDATE claims SET lease_at = '2000-01-01 00:00:00' "
            "WHERE claim_id = 'CLM-1001'"
        )

    continuation_id = database.recover_expired_run(run_id, 300)
    old_run = database.get_run(run_id)
    assert continuation_id is not None
    recovery = database.get_run(continuation_id)
    claim = database.get_claim_record("CLM-1001")

    assert old_run is not None
    assert old_run["status"] == "interrupted"
    assert "lease expired" in old_run["summary"]
    assert recovery is not None
    assert recovery["run_type"] == "recovery"
    assert recovery["selection_complete"] == 1
    assert recovery["todos"][0]["claim_id"] == "CLM-1001"
    assert recovery["todos"][0]["status"] == "in progress"
    assert recovery["todos"][0]["attempt_count"] == 1
    assert claim is not None
    assert claim["status"] == "in progress"
    assert claim["run_id"] == continuation_id
    assert claim["thread_id"] == "saved-claim-thread"
    assert claim["failure_count"] == 0

    resumed_failure = database.fail_claim_attempt(
        continuation_id, "CLM-1001", "Recovery attempt failed"
    )
    claim_after = database.get_claim_record("CLM-1001")

    assert resumed_failure["failure_count"] == 1
    assert resumed_failure["retry"] is True
    assert claim_after is not None
    assert claim_after["status"] == "waiting"
    assert claim_after["failure_count"] == 1


def test_recovery_uses_saved_decision_state_to_resume_only_incomplete_work(
    client: TestClient,
):
    run_id = "partial-decision-crash"
    database.create_run(run_id, "queue", 2)
    database.select_run_claims(run_id, ["CLM-1001", "CLM-1002"])
    todos = required_run(run_id)["todos"]
    for todo in todos:
        database.set_todo_status(todo["todo_id"], "in progress")
    database.save_decision(
        "CLM-1001",
        {"outcome": "refer", "payout_inr": 0.0, "needs_sign_off": False},
        "decided",
    )
    database.save_decision(
        "CLM-1002",
        {"outcome": "approve", "payout_inr": 500.0, "needs_sign_off": False},
        "decided",
    )
    with database.connection() as db:
        db.execute(
            "UPDATE runs SET heartbeat_at = '2000-01-01 00:00:00' WHERE run_id = ?",
            (run_id,),
        )

    continuation_id = database.recover_expired_run(run_id, 300)
    old_run = database.get_run(run_id)
    assert continuation_id is not None
    continuation = database.get_run(continuation_id)
    completed_claim = database.get_claim_record("CLM-1001")
    resumed_claim = database.get_claim_record("CLM-1002")

    assert old_run is not None
    assert [todo["status"] for todo in old_run["todos"]] == ["done", "to do"]
    assert continuation is not None
    assert [todo["claim_id"] for todo in continuation["todos"]] == ["CLM-1002"]
    assert completed_claim is not None
    assert completed_claim["status"] == "decided"
    assert completed_claim["failure_count"] == 0
    assert resumed_claim is not None
    assert resumed_claim["decision"]["outcome"] == "approve"
    assert resumed_claim["status"] == "in progress"
    assert resumed_claim["failure_count"] == 0
    assert continuation["todos"][0]["status"] == "in progress"


def test_new_run_recovers_expired_run_before_accepting_queue_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run_id = "expired-before-new-run"
    database.create_run(run_id, "queue", 1)
    database.select_run_claims(run_id, ["CLM-1001"])
    todo = required_run(run_id)["todos"][0]
    database.set_todo_status(todo["todo_id"], "in progress")
    with database.connection() as db:
        db.execute(
            "UPDATE runs SET heartbeat_at = '2000-01-01 00:00:00' WHERE run_id = ?",
            (run_id,),
        )
        db.execute(
            "UPDATE claims SET lease_at = '2000-01-01 00:00:00' "
            "WHERE claim_id = 'CLM-1001'"
        )

    started_threads = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            started_threads.append((target, args, daemon))

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(main, "Thread", DeferredThread)
    monkeypatch.setattr(main, "run_queue_agent", finish_stub_queue)
    response = client.post("/api/agent/runs", json={"max_claims": 1})
    current = client.get("/api/agent/runs/current").json()["run"]
    recovery_id = started_threads[0][1][0]
    recovery_run = client.get(f"/api/agent/runs/{recovery_id}").json()
    claim = client.get("/api/claims/CLM-1001").json()

    assert response.status_code == 200
    assert required_run(run_id)["status"] == "interrupted"
    assert recovery_run["run_type"] == "recovery"
    assert recovery_run["status"] == "completed"
    assert claim["status"] == "decided"
    assert current is None
    assert len(started_threads) == 1


def test_swarm_dispatcher_and_helpers_cover_new_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "swarm.sqlite3")

    assert dependencies.get_db() is not None
    assert (
        views.to_claim(
            {
                "claim": {"claim_id": "CLM-1001"},
                "status": "decided",
                "decision": "approve",
                "payment": {"amount": 130},
                "sign_off_handler_id": "HND-01",
                "sign_off_at": "2024-01-01",
                "thread_id": "thread-1",
                "policy": {"policy_id": "POL-1"},
                "customer": {"customer_id": "CUST-1"},
            }
        )["claim_id"]
        == "CLM-1001"
    )
    assert views.to_summary(
        {
            "run_id": "run-1",
            "status": "finished",
            "summary": "done",
            "max_claims": 3,
            "run_type": "dispatch",
        }
    ) == {
        "run_id": "run-1",
        "status": "finished",
        "summary": "done",
        "max_claims": 3,
        "run_type": "dispatch",
    }
    assert lookups.find_run([{"run_id": "run-1"}], "run-1") == {"run_id": "run-1"}
    assert lookups.claim_not_found_message("CLM-9999") == "Claim CLM-9999 not found"
    assert lookups.run_not_found_message("run-9") == "Run run-9 not found"


def test_swarm_run_routes_cover_dispatch_and_evaluation_operations(
    monkeypatch: pytest.MonkeyPatch,
):
    api = FastAPI()
    api.include_router(runs.router)
    client = TestClient(api)

    monkeypatch.setattr(
        runs.agent_service, "start_dispatch", lambda agents: {"agents": agents}
    )
    response = client.post("/api/dispatches", json={"agents": 5})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "agents": 5}

    monkeypatch.setattr(
        runs.agent_service,
        "start_dispatch",
        lambda agents: (_ for _ in ()).throw(
            runs.agent_service.AgentServiceConflict(
                "A dispatch or an evaluation is already running"
            )
        ),
    )
    response = client.post("/api/dispatches", json={"agents": 5})
    assert response.status_code == 409
    assert response.json()["detail"] == "A dispatch or an evaluation is already running"

    monkeypatch.setattr(
        runs.agent_service,
        "start_dispatch",
        lambda agents: (_ for _ in ()).throw(
            RuntimeError("The agent's service isn't running")
        ),
    )
    response = client.post("/api/dispatches", json={"agents": 5})
    assert response.status_code == 503

    monkeypatch.setattr(
        runs.agent_service,
        "start_evaluation",
        lambda evaluation_set, tiny, agents: {
            "evaluation_set": evaluation_set,
            "tiny": tiny,
            "agents": agents,
        },
    )
    response = client.post(
        "/api/evaluations",
        json={"evaluation_set": "held_out", "tiny": True, "agents": 3},
    )
    assert response.status_code == 202
    assert response.json()["evaluation_set"] == "held_out"

    monkeypatch.setattr(database, "list_evaluations", lambda: [])
    monkeypatch.setattr(
        database,
        "create_dispatch",
        lambda run_id, agents, model, reasoning_effort: {
            "run_id": run_id,
            "status": "running",
            "agents": agents,
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
    )
    monkeypatch.setattr(
        database,
        "create_evaluation",
        lambda run_id, evaluation_set, tiny, agents, model, reasoning_effort: {
            "run_id": run_id,
            "status": "running",
            "evaluation_set": evaluation_set,
            "tiny": tiny,
            "agents": agents,
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
    )
    monkeypatch.setattr(database, "record_run_claims", lambda run_id, claim_ids: None)
    monkeypatch.setattr(database, "finish_run_summary", lambda run_id, summary: None)
    monkeypatch.setattr(
        database, "get_run", lambda run_id: {"run_id": run_id, "status": "running"}
    )
    monkeypatch.setattr(
        database,
        "get_latest_dispatch",
        lambda: {"run_id": "dispatch-1", "status": "running", "summary": None},
    )
    monkeypatch.setattr(
        database,
        "list_evaluations",
        lambda: [
            {
                "run_id": "eval-1",
                "status": "running",
                "claims": [
                    {
                        "handler_decision": "approve",
                        "handler_payout": 10.0,
                        "decision": None,
                        "seconds": 0.0,
                    }
                ],
                "model_cost": 0.0,
                "created_at": "2026-01-01T00:00:00+00:00",
                "finished_at": None,
            }
        ],
    )
    monkeypatch.setattr(database, "get_evaluation", lambda run_id: None)

    response = client.post(
        "/api/runs/dispatches",
        json={
            "run_id": "dispatch-1",
            "agents": 2,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    response = client.put(
        f"/api/runs/{run_id}/claims", json={"claim_ids": ["CLM-1001", "CLM-1002"]}
    )
    assert response.status_code == 200
    assert response.json()["claim_ids"] == ["CLM-1001", "CLM-1002"]

    response = client.post(f"/api/runs/{run_id}/finish", json={"summary": "finished"})
    assert response.status_code == 200
    assert response.json()["summary"] == "finished"

    response = client.get("/api/dispatches/latest")
    assert response.status_code == 200
    assert response.json()["run_id"] == "dispatch-1"

    response = client.get("/api/evaluations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    response = client.get(f"/api/evaluations/{run_id}")
    assert response.status_code == 404


def test_run_todo_plan_requires_the_selected_claims(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "process_queue_run", lambda run_id: None)
    run_response = client.post("/api/agent/runs", json={"max_claims": 2})
    run_id = run_response.json()["run_id"]
    selected = database.list_waiting_claim_ids(2)
    client.post(f"/api/agent/runs/{run_id}/select", json={"claim_ids": selected})
    run = client.get("/api/agent/runs/current").json()["run"]
    selected = [item["claim_id"] for item in run["todos"]]

    valid = client.put(
        f"/api/agent/runs/{run_id}/todos",
        json={
            "items": [
                {"claim_id": selected[1], "description": "Review second claim"},
                {"claim_id": selected[0], "description": "Review first claim"},
            ]
        },
    )
    invalid = client.put(
        f"/api/agent/runs/{run_id}/todos",
        json={"items": [{"claim_id": "CLM-9999", "description": "Other claim"}]},
    )
    planned = client.get("/api/agent/runs/current").json()["run"]["todos"]

    assert valid.status_code == 200
    assert invalid.status_code == 422
    assert [item["claim_id"] for item in planned] == list(reversed(selected))


def test_todo_statuses_are_persisted_and_invalid_status_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "process_queue_run", lambda run_id: None)
    response = client.post("/api/agent/runs", json={"max_claims": 1})
    run_id = response.json()["run_id"]
    client.post(
        f"/api/agent/runs/{run_id}/select",
        json={"claim_ids": database.list_waiting_claim_ids(1)},
    )
    run = client.get("/api/agent/runs/current").json()["run"]
    todo_id = run["todos"][0]["todo_id"]

    for status in ("to do", "in progress", "done", "left for handler"):
        if status == "done":
            database.save_decision(run["todos"][0]["claim_id"], decision(), "decided")
        if status == "left for handler":
            database.set_claim_status(run["todos"][0]["claim_id"], "needs handler")
        update = client.patch(
            f"/api/agent/runs/{run_id}/todos/{todo_id}", json={"status": status}
        )
        assert update.status_code == 200
        assert (
            client.get("/api/agent/runs/current").json()["run"]["todos"][0]["status"]
            == status
        )

    invalid_state = client.patch(
        f"/api/agent/runs/{run_id}/todos/{todo_id}", json={"status": "blocked"}
    )
    wrong_run = client.patch(
        f"/api/agent/runs/unknown/todos/{todo_id}", json={"status": "done"}
    )

    assert invalid_state.status_code == 422
    assert wrong_run.status_code == 404


def test_needs_handler_claim_appears_in_queue(client: TestClient):
    database.set_claim_status("CLM-1001", "needs handler")

    response = client.get("/api/claims")
    claims = {claim["claim_id"]: claim for claim in response.json()["claims"]}

    assert claims["CLM-1001"]["status"] == "needs handler"


def test_decision_signoff_and_payment_writes_are_idempotent(client: TestClient):
    stored_decision = decision()

    database.save_decision("CLM-1001", stored_decision, "waiting for sign-off")
    database.save_decision("CLM-1001", stored_decision, "waiting for sign-off")
    database.sign_off_claim("CLM-1001", "HND-01")
    database.sign_off_claim("CLM-1001", "HND-01")
    database.save_payment("CLM-1001", 0.0)
    database.save_payment("CLM-1001", 0.0)

    with database.connection() as db:
        decision_row = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1001'"
        ).fetchone()
        signoff_row = db.execute(
            "SELECT COUNT(*) FROM signoffs WHERE claim_id = 'CLM-1001'"
        ).fetchone()
        payment_row = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1001'"
        ).fetchone()

    assert decision_row is not None
    assert signoff_row is not None
    assert payment_row is not None
    assert decision_row[0] == 1
    assert signoff_row[0] == 1
    assert payment_row[0] == 1


def test_named_claim_creates_a_one_item_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "process_queue_run", lambda run_id: None)

    response = client.post("/api/claims/CLM-1001/run")
    run = client.get("/api/agent/runs/current").json()["run"]

    assert response.json()["status"] == "in progress"
    assert response.json()["run_id"] == run["run_id"]
    assert run["run_type"] == "claim"
    assert run["max_claims"] == 1
    assert [item["claim_id"] for item in run["todos"]] == ["CLM-1001"]
    assert run["todos"][0]["status"] == "to do"


def test_direct_claim_run_starts_without_creating_a_run(client: TestClient):
    response = client.post("/api/claims/CLM-1001/run/direct")
    claim = client.get("/api/claims/CLM-1001").json()

    assert response.status_code == 200
    assert response.json() == {"status": "in progress"}
    assert claim["status"] == "in progress"
    assert database.get_latest_run() is None


def test_queue_cannot_close_with_a_short_batch_or_unresolved_claim(
    client: TestClient,
):
    run_id = "reconcile-run"
    database.create_run(run_id, "queue", 2)
    short_selection = client.post(
        f"/api/agent/runs/{run_id}/select", json={"claim_ids": ["CLM-1001"]}
    )

    assert short_selection.status_code == 422
    assert (
        client.post(
            f"/api/agent/runs/{run_id}/select",
            json={"claim_ids": ["CLM-1001", "CLM-1002"]},
        ).status_code
        == 200
    )
    close = client.post(
        f"/api/agent/runs/{run_id}/complete", json={"summary": "Incomplete."}
    )

    assert close.status_code == 409
    assert "unfinished work" in close.json()["detail"]


def test_initialize_deduplicates_old_decisions_and_adds_unique_constraints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = tmp_path / "pilot.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    first_decision = decision("Keep the earliest record.")
    duplicate_decision = decision("Remove the later duplicate.")
    other_decision = decision("Keep the other claim's decision.")
    claims = database.read_json("claims.json")
    policies = {row["policy_id"]: row for row in database.read_json("policies.json")}
    customers = {
        row["customer_id"]: row for row in database.read_json("customers.json")
    }
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY,
                policy_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                source_json TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                customer_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                decision_json TEXT,
                sign_off_handler_id TEXT,
                payment_json TEXT
            );
            CREATE TABLE events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                step TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                decision_json TEXT NOT NULL
            );
            CREATE TABLE payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                payment_json TEXT NOT NULL
            );
            """
        )
        for claim in claims:
            decision_json = (
                json.dumps(duplicate_decision)
                if claim["claim_id"] == "CLM-1001"
                else json.dumps(other_decision)
                if claim["claim_id"] == "CLM-1002"
                else None
            )
            db.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    claim["claim_id"],
                    claim["policy_id"],
                    claim["customer_id"],
                    json.dumps(claim),
                    json.dumps(policies[claim["policy_id"]]),
                    json.dumps(customers[claim["customer_id"]]),
                    "decided" if decision_json is not None else "waiting",
                    decision_json,
                    None,
                    None,
                ),
            )
        db.executemany(
            "INSERT INTO decisions (claim_id, decision_json) VALUES (?, ?)",
            [
                ("CLM-1001", json.dumps(first_decision)),
                ("CLM-1001", json.dumps(duplicate_decision)),
                ("CLM-1002", json.dumps(other_decision)),
            ],
        )
        db.executemany(
            "INSERT INTO payments (claim_id, payment_json) VALUES (?, ?)",
            [
                (
                    "CLM-1003",
                    json.dumps({"amount_inr": 100.0, "simulated": True}),
                ),
                (
                    "CLM-1003",
                    json.dumps({"amount_inr": 200.0, "simulated": True}),
                ),
            ],
        )

    database.initialize_database()
    record = database.get_claim_record("CLM-1001")
    events = database.list_events("CLM-1001")
    with database.connection() as db:
        decisions = db.execute("SELECT * FROM decisions").fetchall()
        payments = db.execute("SELECT * FROM payments").fetchall()
        indexes = {row["name"] for row in db.execute("PRAGMA index_list(decisions)")}
        payment_indexes = {
            row["name"] for row in db.execute("PRAGMA index_list(payments)")
        }
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO decisions (claim_id, decision_json, idempotency_key) "
                "VALUES (?, ?, ?)",
                ("CLM-1001", json.dumps(duplicate_decision), "duplicate-key"),
            )
        db.execute(
            "INSERT INTO payments (claim_id, payment_json, idempotency_key) "
            "VALUES (?, ?, ?)",
            ("CLM-1001", "{}", "payment-one"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO payments (claim_id, payment_json, idempotency_key) "
                "VALUES (?, ?, ?)",
                ("CLM-1001", "{}", "payment-two"),
            )

    assert record is not None
    assert record["decision"] == first_decision
    assert len(database.list_claim_records()) == 25
    assert len(decisions) == 2
    assert len(payments) == 1
    assert "decisions_claim_unique" in indexes
    assert "decisions_key_unique" in indexes
    assert "payments_claim_unique" in payment_indexes
    assert "payments_key_unique" in payment_indexes
    assert any(event["step"] == "Duplicate record removed" for event in events)
