import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, inspect
from sqlalchemy.pool import NullPool, QueuePool

from backend import database, dependencies, lookups, runs, views


def test_database_uses_normalized_core_tables_and_json_columns(client: TestClient):
    table_names = set(inspect(database.engine()).get_table_names())
    settings = database.get_setting("handlers")

    assert table_names == set(database.metadata.tables)
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
        "run_claims",
        "model_calls",
        "work_times",
        "held_out_claims",
        "handler_decisions",
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


def test_direct_claim_run_starts_without_creating_a_run(client: TestClient):
    response = client.post("/api/claims/CLM-1001/run/direct")
    claim = client.get("/api/claims/CLM-1001").json()

    assert response.status_code == 200
    assert response.json() == {"status": "in progress"}
    assert claim["status"] == "in progress"
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


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
