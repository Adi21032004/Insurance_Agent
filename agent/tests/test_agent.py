import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from agent import main
from agent.testing import (
    StubModel,
    stub_model,
    stub_over_limit_model,
    stub_queue_model,
    stub_refer_model,
    tool_message,
)
from backend import database
from backend import main as backend_main
from backend.main import app


def use_test_database(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")


def test_agent_tools_read_claim_and_policy_through_backend(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1001", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))

    claim = json.loads(main.get_claim_context.invoke({"claim_id": "CLM-1001"}))
    matches = json.loads(
        main.search_claim_knowledge.invoke(
            {
                "claim_id": "CLM-1001",
                "source": "policy_wording",
                "query": "Trip Delay",
            }
        )
    )
    section = json.loads(
        main.read_claim_section.invoke(
            {
                "claim_id": "CLM-1001",
                "source": "policy_wording",
                "section_id": "policy_wording:03",
            }
        )
    )

    assert claim["claim"]["amount_claimed_inr"] == 34860.0
    assert claim["customer"]["name"] == "Maya Patel"
    assert "policy_wording" not in claim
    assert any(match["section_id"] == "policy_wording:03" for match in matches)
    assert "mechanical or crew problem" in section["content"]
    assert "$" not in section["content"]


def test_agent_has_no_direct_seed_data_dependency():
    source = Path(main.__file__).read_text(encoding="utf-8")

    assert "backend.database" not in source
    assert "read_json" not in source
    assert "data/" not in source


def test_run_uses_instructions_api_and_records_decision(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1001", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(main, "model", lambda claim_id=None: stub_model("CLM-1001"))

    reply = main.run("CLM-1001", "thread-1")

    assert reply.startswith("Approved for ₹34,860.00")
    record = database.get_claim_record("CLM-1001")
    assert record is not None
    assert record["decision"]["policy_section"] == "Section 3: Trip Delay"
    assert record["decision"]["payout_inr"] == 34860.0
    assert record["decision"]["manual_version"] == "2026.2"
    assert {item["source"] for item in record["decision"]["sections_read"]} == {
        "claims_manual",
        "policy_wording",
    }
    assert record["payment"] == {"amount_inr": 34860.0, "simulated": True}
    assert "Source section read" in {
        event["step"] for event in database.list_events("CLM-1001")
    }
    assert len(database.list_source_reads("CLM-1001")) == 2


def test_queue_run_plans_order_and_uses_fresh_claim_helpers(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(backend_main, "process_queue_run", lambda run_id: None)
    with TestClient(app) as client:
        started = client.post("/api/agent/runs", json={"max_claims": 5})
        run_id = started.json()["run_id"]
        waiting_before = [
            claim["claim_id"]
            for claim in client.get("/api/claims").json()["claims"]
            if claim["status"] == "waiting"
        ]

    expected_order = list(reversed(waiting_before[:5]))
    helper_threads = []
    helper_agents = []
    original_helper = main.run_claim_helper
    original_factory = main.create_agent

    def tracked_helper(claim_id, thread_id):
        helper_threads.append((claim_id, thread_id))
        return original_helper(claim_id, thread_id)

    def tracked_factory(*args, **kwargs):
        created = original_factory(*args, **kwargs)
        helper_agents.append(created)
        return created

    monkeypatch.setattr(main, "run_claim_helper", tracked_helper)
    monkeypatch.setattr(main, "create_agent", tracked_factory)
    monkeypatch.setattr(main, "queue_model", stub_queue_model)

    def claim_model(claim_id=None):
        assert claim_id is not None
        record = database.get_claim_record(claim_id)
        assert record is not None
        return stub_refer_model(claim_id, record["claim"]["claim_type"])

    monkeypatch.setattr(main, "model", claim_model)

    summary = main.run_queue(run_id, f"{run_id}-thread")

    with TestClient(app) as client:
        completed = client.get(f"/api/agent/runs/{run_id}").json()
        claims = {
            claim["claim_id"]: claim
            for claim in client.get("/api/claims").json()["claims"]
        }

    assert [todo["claim_id"] for todo in completed["todos"]] == expected_order
    assert {todo["status"] for todo in completed["todos"]} == {"done"}
    assert completed["status"] == "completed"
    assert completed["summary"] == summary
    assert all(claims[claim_id]["status"] == "decided" for claim_id in expected_order)
    assert sum(claim["status"] == "waiting" for claim in claims.values()) == 20
    assert [claim_id for claim_id, _ in helper_threads] == expected_order
    assert len({thread_id for _, thread_id in helper_threads}) == 5
    assert len({id(agent) for agent in helper_agents}) == 6
    for claim_id in expected_order:
        record = database.get_claim_record(claim_id)
        assert record is not None
        assert record["decision"]["manual_version"] == "2026.2"
        assert {read["source"] for read in database.list_source_reads(claim_id)} == {
            "claims_manual",
            "policy_wording",
        }


def test_queue_retries_one_failed_claim_without_spending_another_slot(
    monkeypatch, tmp_path
):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(backend_main, "process_queue_run", lambda run_id: None)
    with TestClient(app) as client:
        run_id = client.post("/api/agent/runs", json={"max_claims": 5}).json()["run_id"]
        waiting_before = [
            claim["claim_id"]
            for claim in client.get("/api/claims").json()["claims"]
            if claim["status"] == "waiting"
        ]
    first_claim = list(reversed(waiting_before[:5]))[0]
    calls = []
    original_helper = main.run_claim_helper
    monkeypatch.setattr(main, "queue_model", stub_queue_model)

    def claim_model(claim_id=None):
        record = database.get_claim_record(claim_id)
        assert record is not None
        return stub_refer_model(claim_id, record["claim"]["claim_type"])

    def fail_once(claim_id, thread_id):
        calls.append((claim_id, thread_id))
        if claim_id == first_claim and thread_id.endswith("attempt1"):
            raise TimeoutError("Stubbed claim timeout")
        return original_helper(claim_id, thread_id)

    monkeypatch.setattr(main, "model", claim_model)
    monkeypatch.setattr(main, "run_claim_helper", fail_once)

    summary = main.run_queue(run_id, f"{run_id}-queue")
    run = database.get_run(run_id)
    claim = database.get_claim_record(first_claim)
    queue = {item["claim"]["claim_id"]: item for item in database.list_claim_records()}

    assert run is not None
    assert run["status"] == "completed"
    assert run["summary"] == summary
    assert len(run["todos"]) == 5
    retried_todo = next(
        todo for todo in run["todos"] if todo["claim_id"] == first_claim
    )
    assert retried_todo["status"] == "done"
    assert retried_todo["attempt_count"] == 2
    assert claim is not None
    assert claim["failure_count"] == 0
    assert claim["status"] == "decided"
    assert [thread for item, thread in calls if item == first_claim] == [
        f"{run_id}-queue-{first_claim}-attempt1",
        f"{run_id}-queue-{first_claim}-attempt2",
    ]
    assert sum(item["status"] == "waiting" for item in queue.values()) == 20
    assert "5 referred" in summary


def test_second_failure_routes_one_claim_to_handler_and_continues(
    monkeypatch, tmp_path
):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(backend_main, "process_queue_run", lambda run_id: None)
    with TestClient(app) as client:
        run_id = client.post("/api/agent/runs", json={"max_claims": 5}).json()["run_id"]
        waiting_before = [
            claim["claim_id"]
            for claim in client.get("/api/claims").json()["claims"]
            if claim["status"] == "waiting"
        ]
    selected = list(reversed(waiting_before[:5]))
    original_helper = main.run_claim_helper
    failures = []
    monkeypatch.setattr(main, "queue_model", stub_queue_model)

    def claim_model(claim_id=None):
        record = database.get_claim_record(claim_id)
        assert record is not None
        return stub_refer_model(claim_id, record["claim"]["claim_type"])

    def always_fail_first(claim_id, thread_id):
        if claim_id == selected[0]:
            failures.append(thread_id)
            raise RuntimeError("Stubbed tool failure")
        return original_helper(claim_id, thread_id)

    monkeypatch.setattr(main, "model", claim_model)
    monkeypatch.setattr(main, "run_claim_helper", always_fail_first)

    summary = main.run_queue(run_id, f"{run_id}-queue")
    run = database.get_run(run_id)
    failed_claim = database.get_claim_record(selected[0])
    decisions = [database.get_claim_record(claim_id) for claim_id in selected[1:]]

    assert run is not None
    assert run["status"] == "completed"
    assert run["todos"][0]["status"] == "left for handler"
    assert failed_claim is not None
    assert failed_claim["status"] == "needs handler"
    assert failed_claim["failure_count"] == 2
    assert len(failures) == 2
    assert failures[0].endswith("attempt1")
    assert failures[1].endswith("attempt2")
    assert all(
        claim is not None and claim["decision"] is not None for claim in decisions
    )
    assert "1 need handler attention" in summary


def test_saved_decision_resumes_payment_without_creating_a_second_decision(
    monkeypatch, tmp_path
):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(backend_main, "process_queue_run", lambda run_id: None)
    with TestClient(app) as client:
        run_id = client.post("/api/agent/runs", json={"max_claims": 1}).json()["run_id"]
    monkeypatch.setattr(main, "queue_model", stub_queue_model)
    saved_once = []

    def save_then_timeout(claim_id, thread_id):
        if thread_id.endswith("attempt1"):
            decision = {
                "outcome": "approve",
                "reason": "The saved approval is ready for its simulated payment.",
                "policy_section": "Section 3: Trip Delay",
                "calculation": [{"step": "Eligible expenses", "amount_inr": 100.0}],
                "payout_inr": 100.0,
                "needs_sign_off": False,
            }
            database.save_decision(claim_id, decision, "decided")
            saved_once.append(claim_id)
            raise TimeoutError("Stubbed timeout after decision storage")
        return original_helper(claim_id, thread_id)

    original_helper = main.run_claim_helper
    monkeypatch.setattr(main, "run_claim_helper", save_then_timeout)

    main.run_queue(run_id, f"{run_id}-queue")
    claim = database.get_claim_record("CLM-1001")
    with database.connection() as db:
        decisions = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]
        payments = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]

    assert saved_once == ["CLM-1001"]
    assert claim is not None
    assert claim["decision"]["outcome"] == "approve"
    assert claim["payment"] == {"amount_inr": 100.0, "simulated": True}
    assert decisions == 1
    assert payments == 1


def test_two_failures_resuming_saved_payment_keep_decision_for_handler(
    monkeypatch, tmp_path
):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(backend_main, "process_queue_run", lambda run_id: None)
    with TestClient(app) as client:
        run_id = client.post("/api/agent/runs", json={"max_claims": 1}).json()["run_id"]
    monkeypatch.setattr(main, "queue_model", stub_queue_model)
    saved_decision = {
        "outcome": "approve",
        "reason": "The evidence supports this approval.",
        "policy_section": "Section 3: Trip Delay",
        "calculation": [],
        "payout_inr": 100.0,
        "needs_sign_off": False,
    }
    original_helper = main.run_claim_helper
    failed_payments = []

    def failed_payment_tool(*args, **kwargs):
        failed_payments.append(True)
        raise RuntimeError("Stubbed payment tool failure")

    def fail_after_decision(claim_id, thread_id):
        if thread_id.endswith("attempt1"):
            database.save_decision(claim_id, saved_decision, "decided")
            raise TimeoutError("Stubbed failure after decision")
        return original_helper(claim_id, thread_id)

    monkeypatch.setattr(main.pay_claim, "func", failed_payment_tool)
    monkeypatch.setattr(main, "run_claim_helper", fail_after_decision)

    summary = main.run_queue(run_id, f"{run_id}-queue")
    run = database.get_run(run_id)
    claim = database.get_claim_record("CLM-1001")
    with database.connection() as db:
        decisions = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]
        payments = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = 'CLM-1001'"
        ).fetchone()[0]

    assert run is not None
    assert run["status"] == "completed"
    assert run["todos"][0]["status"] == "left for handler"
    assert claim is not None
    assert claim["status"] == "needs handler"
    assert claim["decision"] == saved_decision
    assert claim["failure_count"] == 2
    assert len(failed_payments) == 1
    assert decisions == 1
    assert payments == 0
    assert "1 need handler attention" in summary


def test_agent_records_decline_with_policy_evidence(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1001", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))

    messages = iter(
        [
            tool_message("get_claim_context", {"claim_id": "CLM-1001"}, "1"),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": "CLM-1001",
                    "source": "policy_wording",
                    "query": "general exclusions",
                },
                "2",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": "CLM-1001",
                    "source": "policy_wording",
                    "section_id": "policy_wording:09",
                },
                "3",
            ),
            tool_message(
                "search_claim_knowledge",
                {
                    "claim_id": "CLM-1001",
                    "source": "claims_manual",
                    "query": "conflicting documents investigation",
                },
                "4",
            ),
            tool_message(
                "read_claim_section",
                {
                    "claim_id": "CLM-1001",
                    "source": "claims_manual",
                    "section_id": "claims_manual:08",
                },
                "5",
            ),
            tool_message(
                "record_claim_progress",
                {"claim_id": "CLM-1001", "step": "Evidence", "detail": "Reviewing."},
                "6",
            ),
            tool_message(
                "record_claim_decision",
                {
                    "claim_id": "CLM-1001",
                    "outcome": "decline",
                    "reason": (
                        "This claim is outside the covered event. "
                        "The documents confirm the exclusion."
                    ),
                    "policy_section": "Section 9: General Exclusions",
                    "benefit": "trip_delay",
                    "items": [],
                    "paid_by_others_inr": 0,
                },
                "7",
            ),
            AIMessage(content="Declined under Section 9: General Exclusions."),
        ]
    )
    monkeypatch.setattr(
        main, "model", lambda claim_id=None: StubModel(messages=messages)
    )

    reply = main.run("CLM-1001", "thread-2")

    assert "Section 9" in reply
    record = database.get_claim_record("CLM-1001")
    assert record is not None
    assert record["decision"]["outcome"] == "decline"
    assert record["decision"]["payout_inr"] == 0


def test_agent_refers_when_policy_evidence_is_incomplete(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1002", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(
        main, "model", lambda claim_id=None: stub_refer_model("CLM-1002")
    )

    reply = main.run("CLM-1002", "thread-4")

    assert "Section 5" in reply
    record = database.get_claim_record("CLM-1002")
    assert record is not None
    assert record["decision"]["outcome"] == "refer"
    assert record["status"] == "decided"


def test_agent_payment_tool_cannot_bypass_handler_signoff(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1003", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(
        main, "model", lambda claim_id=None: stub_over_limit_model("CLM-1003")
    )

    reply = main.run("CLM-1003", "thread-3")

    assert "Payment is waiting for handler sign-off" in reply
    record = database.get_claim_record("CLM-1003")
    assert record is not None
    assert record["decision"]["payout_inr"] == 311250.0
    assert record["status"] == "waiting for sign-off"
    assert record["payment"] is None


def test_model_respects_model_name_and_defaults(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_AGENT_TEST_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default_model = main.model()
    assert isinstance(default_model, ChatOpenAI)
    assert default_model.model_name == "deepseek/deepseek-v4.1-flash"
    assert default_model.reasoning_effort == "low"
    assert default_model.request_timeout == main.CLAIM_TIMEOUT_SECONDS
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    configured_model = main.model()
    assert isinstance(configured_model, ChatOpenAI)
    assert configured_model.model_name == "openrouter/free"


def test_test_mode_model_is_scripted(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_AGENT_TEST_MODE", "1")
    monkeypatch.setenv("UNDERCOVER_TEST_CLAIM_ID", "CLM-1001")

    assert isinstance(main.model(), StubModel)
