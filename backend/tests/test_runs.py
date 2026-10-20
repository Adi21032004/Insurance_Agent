import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import database, main
from backend.main import app


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
    assert claim is not None
    assert claim["status"] == "in progress"
    assert claim["run_id"] == continuation_id
    assert claim["failure_count"] == 1

    second_failure = database.fail_claim_attempt(
        continuation_id, "CLM-1001", "Recovery attempt failed"
    )
    claim_after = database.get_claim_record("CLM-1001")

    assert second_failure["retry"] is False
    assert claim_after is not None
    assert claim_after["status"] == "needs handler"
    assert claim_after["failure_count"] == 2


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
    assert resumed_claim["failure_count"] == 1


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
        decision_count = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]
        signoff_count = db.execute(
            "SELECT COUNT(*) FROM signoffs WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]
        payment_count = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]

    assert decision_count == 1
    assert signoff_count == 1
    assert payment_count == 1


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
