from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver

from agent import api, harness
from agent.harness import (
    build_claim_graph,
    carry_on,
    checkpointer,
    process_saved_run,
    run_queue,
    work_claim,
)
from agent.testing import StubModel, durable_stub_model, tool_message
from agent.tools import CLAIM_TOOLS
from backend import agent_service, database
from backend.main import app


@pytest.fixture
def backend_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("UNDERCOVER_AGENT_TEST_MODE", "1")
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "durable.sqlite3")
    monkeypatch.setattr(agent_service, "resume_claim", lambda claim_id: None)
    monkeypatch.setattr(agent_service, "run_queue", lambda run_id: None)
    monkeypatch.setattr(api, "backend", lambda: TestClient(app))
    with TestClient(app) as client:
        yield client


def test_claim_graph_uses_the_durable_tools_and_interrupt_node(
    backend_client: TestClient,
):
    graph = build_claim_graph(durable_stub_model("CLM-1001"), InMemorySaver())

    assert set(graph.nodes) == {
        "__start__",
        "agent",
        "tools",
        "wait_for_sign_off",
        "pay",
    }
    assert [item.name for item in CLAIM_TOOLS] == [
        "get_claim",
        "get_policy",
        "get_claim_history",
        "search_documents",
        "read_section",
        "calculate_payout",
        "record_decision",
    ]


def test_claim_invokes_have_root_trace_metadata_and_sync_durability(
    backend_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    parent_context = ContextVar("test_parent_context", default="outside")
    parent_context.set("queue run")
    invocations: list[dict[str, Any]] = []
    root_runs: list[dict[str, Any]] = []

    class RecordingCallbackHandler(BaseCallbackHandler):
        def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
            if kwargs.get("parent_run_id") is None:
                root_runs.append(
                    {
                        "metadata": kwargs.get("metadata", {}),
                        "parent": parent_context.get(),
                    }
                )

    class RecordingGraph:
        def __init__(self, graph: Any):
            self.graph = graph

        def invoke(self, value: Any, config: Any, **kwargs: Any):
            invocations.append(
                {
                    "config": config,
                    "kwargs": kwargs,
                }
            )
            return self.graph.invoke(value, config, **kwargs)

        def get_state(self, config: Any):
            return self.graph.get_state(config)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret-key")
    monkeypatch.setattr(harness, "CallbackHandler", RecordingCallbackHandler)
    saver = InMemorySaver()
    for claim_id in ("CLM-1001", "CLM-1002"):
        database.set_claim_status(claim_id, "in progress")
        graph = RecordingGraph(build_claim_graph(durable_stub_model(claim_id), saver))
        assert work_claim(claim_id, graph).startswith(claim_id)

    database.set_claim_status("CLM-1003", "in progress")
    resumed_graph = RecordingGraph(
        build_claim_graph(durable_stub_model("CLM-1003"), saver)
    )
    assert work_claim("CLM-1003", resumed_graph).endswith("waiting for sign-off")
    backend_client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    harness.carry_on("CLM-1003", resumed_graph)

    assert len(root_runs) == len(invocations) == 4
    assert [record["metadata"]["langfuse_session_id"] for record in root_runs] == [
        f"CLM-1001:{invocations[0]['config']['configurable']['thread_id']}",
        f"CLM-1002:{invocations[1]['config']['configurable']['thread_id']}",
        f"CLM-1003:{invocations[2]['config']['configurable']['thread_id']}",
        f"CLM-1003:{invocations[3]['config']['configurable']['thread_id']}",
    ]
    assert [record["metadata"]["langfuse_trace_name"] for record in root_runs] == [
        "Work claim",
        "Work claim",
        "Work claim",
        "Carry on",
    ]
    assert all(record["parent"] == "outside" for record in root_runs)
    assert all(call["kwargs"]["durability"] == "sync" for call in invocations)
    assert all(
        isinstance(call["config"]["callbacks"][0], RecordingCallbackHandler)
        for call in invocations
    )


def test_within_limit_approval_is_paid_by_the_graph(backend_client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")
    saver = InMemorySaver()
    graph = build_claim_graph(durable_stub_model("CLM-1001"), saver)

    report = work_claim("CLM-1001", graph)
    record = backend_client.get("/api/claims/CLM-1001").json()

    assert "approve" in report
    assert record["status"] == "decided"
    assert record["decision"]["payout_inr"] == 34860.0
    assert record["payment"]["amount_inr"] == 34860.0
    assert "pay_claim" not in [event["detail"] for event in record["progress"]]
    assert carry_on("CLM-1001", graph) == report


def test_sign_off_resumes_the_saved_graph_and_pays_once(backend_client: TestClient):
    database.set_claim_status("CLM-1003", "in progress")
    saver = InMemorySaver()
    graph = build_claim_graph(durable_stub_model("CLM-1003"), saver)

    report = work_claim("CLM-1003", graph)
    held = backend_client.get("/api/claims/CLM-1003").json()
    thread_id = held["thread_id"]
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})

    assert report.endswith("waiting for sign-off")
    assert held["status"] == "waiting for sign-off"
    assert held["payment"] is None
    assert snapshot.next == ("wait_for_sign_off",)
    assert len(snapshot.interrupts) == 1

    signed_off = backend_client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert signed_off.json()["status"] == "in progress"
    assert backend_client.get("/api/claims/CLM-1003").json()["payment"] is None

    resumed_report = carry_on("CLM-1003", graph)
    paid = backend_client.get("/api/claims/CLM-1003").json()

    assert resumed_report.endswith("₹311,250.00")
    assert paid["status"] == "decided"
    assert paid["payment"]["amount_inr"] == 311250.0
    assert paid["payment"]["signed_off_by"] == "Dana Whitfield"
    assert paid["payment"]["signed_off_at"] == paid["sign_off_at"]
    assert paid["thread_id"] == thread_id


def test_claim_restart_continues_from_saved_tool_results(backend_client: TestClient):
    database.set_claim_status("CLM-1001", "in progress")
    saver = InMemorySaver()
    interrupted_model = StubModel(
        messages=iter(
            [
                tool_message("get_claim", {}, "before-restart-1"),
                tool_message("get_policy", {}, "before-restart-2"),
            ]
        )
    )
    first_graph = build_claim_graph(interrupted_model, saver)

    with pytest.raises(RuntimeError, match="generator raised StopIteration"):
        work_claim("CLM-1001", first_graph)
    stopped = backend_client.get("/api/claims/CLM-1001").json()
    thread_id = stopped["thread_id"]
    saved = first_graph.get_state({"configurable": {"thread_id": thread_id}})
    prior_tool_results = [
        message for message in saved.values["messages"] if message.type == "tool"
    ]

    assert stopped["status"] == "in progress"
    assert stopped["failure_count"] == 0
    assert len(prior_tool_results) == 2

    resumed_graph = build_claim_graph(durable_stub_model("CLM-1001"), saver)
    report = carry_on("CLM-1001", resumed_graph)
    completed = backend_client.get("/api/claims/CLM-1001").json()

    assert "approve" in report
    assert completed["thread_id"] == thread_id
    assert completed["failure_count"] == 0
    assert completed["payment"]["amount_inr"] == 34860.0


def test_expired_run_resumes_claim_on_the_same_thread_and_attempt(
    backend_client: TestClient,
):
    run_id = "durable-recovery-run"
    database.create_run(run_id, "queue", 1)
    database.select_run_claims(run_id, ["CLM-1001"])
    created_run = database.get_run(run_id)
    assert created_run is not None
    todo = created_run["todos"][0]
    database.set_todo_status(todo["todo_id"], "in progress")
    saver = InMemorySaver()
    interrupted_model = StubModel(
        messages=iter(
            [
                tool_message("get_claim", {}, "before-recovery-1"),
                tool_message("get_policy", {}, "before-recovery-2"),
            ]
        )
    )
    first_graph = build_claim_graph(interrupted_model, saver)

    with pytest.raises(RuntimeError, match="generator raised StopIteration"):
        work_claim("CLM-1001", first_graph)
    stopped = backend_client.get("/api/claims/CLM-1001").json()
    thread_id = stopped["thread_id"]
    with database.connection() as db:
        db.execute(
            "UPDATE runs SET heartbeat_at = '2000-01-01 00:00:00' WHERE run_id = ?",
            (run_id,),
        )

    recovery_id = database.recover_expired_run(run_id, 300)
    assert recovery_id is not None
    recovered_run = database.get_run(recovery_id)
    assert recovered_run is not None
    assert recovered_run["todos"][0]["status"] == "in progress"
    assert recovered_run["todos"][0]["attempt_count"] == 1

    resumed_graph = build_claim_graph(durable_stub_model("CLM-1001"), saver)
    process_saved_run(recovery_id, saver, graph=resumed_graph)
    completed = backend_client.get("/api/claims/CLM-1001").json()
    completed_run = database.get_run(recovery_id)

    assert completed["thread_id"] == thread_id
    assert completed["failure_count"] == 0
    assert completed["status"] == "decided"
    assert completed["payment"]["amount_inr"] == 34860.0
    assert completed_run is not None
    assert completed_run["todos"][0]["status"] == "done"
    assert completed_run["todos"][0]["attempt_count"] == 1


def test_payment_saved_before_restart_is_replayed_idempotently(
    backend_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    database.set_claim_status("CLM-1001", "in progress")
    saver = InMemorySaver()
    graph = build_claim_graph(durable_stub_model("CLM-1001"), saver)
    original_pay = api.pay_claim

    def pay_then_stop(claim_id: str, amount_inr: float) -> dict[str, Any]:
        original_pay(claim_id, amount_inr)
        raise KeyboardInterrupt

    monkeypatch.setattr(api, "pay_claim", pay_then_stop)
    with pytest.raises(KeyboardInterrupt):
        work_claim("CLM-1001", graph)
    interrupted = backend_client.get("/api/claims/CLM-1001").json()
    assert interrupted["status"] == "decided"
    assert interrupted["payment"]["amount_inr"] == 34860.0

    monkeypatch.setattr(api, "pay_claim", original_pay)
    report = carry_on("CLM-1001", graph)
    resumed = backend_client.get("/api/claims/CLM-1001").json()
    with database.connection() as db:
        payment_row = db.execute(
            "SELECT COUNT(*) FROM payments WHERE claim_id = ?", ("CLM-1001",)
        ).fetchone()

    assert report.endswith("₹34,860.00")
    assert resumed["payment"]["amount_inr"] == 34860.0
    assert payment_row is not None
    assert payment_row[0] == 1


def test_agent_api_operations_cover_claim_knowledge_payout_and_runs(
    backend_client: TestClient,
):
    claim = api.get_claim("CLM-1001")
    assert api.get_policy("CLM-1001") == claim["policy"]
    assert api.get_claim_history("CLM-1001") == claim["customer"]["previous_claims"]
    assert api.get_latest_run() is None

    database.set_claim_status("CLM-1001", "in progress")
    results = api.search_documents("CLM-1001", "policy_wording", "trip delay")
    assert results
    section = api.read_section("CLM-1001", "policy_wording", results[0]["section_id"])
    api.record_progress("CLM-1001", "Review", "Read the policy wording.")
    payout = api.calculate_payout(
        "CLM-1001",
        "trip_delay",
        [
            {
                "description": "Hotel",
                "amount": 245,
                "quantity": 1,
                "kind": "hotel",
                "nights": 1,
                "hours": 0,
            }
        ],
        0,
    )
    assert payout["payout"] == 245.0
    assert section["section_id"] == results[0]["section_id"]
    assert api.get_instructions()["claim_instructions"]
    api.record_decision(
        "CLM-1001",
        {
            "outcome": "decline",
            "reason": "The evidence does not meet the policy terms.",
            "policy_section": section["heading"],
            "benefit": "trip_delay",
            "lines": [],
            "paid_by_others": 0,
        },
    )

    database.set_claim_status("CLM-1002", "in progress")
    api.record_decision(
        "CLM-1002",
        {
            "outcome": "approve",
            "reason": "The claim is covered.",
            "policy_section": "Section 5: Baggage",
            "benefit": "baggage",
            "lines": [],
            "paid_by_others": 0,
        },
    )
    assert api.pay_claim("CLM-1002", 0)["status"] == "decided"
    assert api.get_agent_claim("CLM-1002")["payment"]["amount_inr"] == 0
    assert api.get_queue_claims()

    run_id = api.create_direct_run(1)
    api.select_run_claims(run_id, ["CLM-1003"])
    api.plan_run_todos(
        run_id, [{"claim_id": "CLM-1003", "description": "Review the claim"}]
    )
    todo_id = api.get_run(run_id)["todos"][0]["todo_id"]
    api.update_run_todo(run_id, todo_id, "in progress")
    assert api.fail_run_attempt(run_id, "CLM-1003", "Temporary outage")["retry"]
    assert api.retry_run_claim(run_id, "CLM-1003", "Retry the review") == 2
    api.save_claim_thread("CLM-1003", "claim-thread-1003")
    api.record_decision(
        "CLM-1003",
        {
            "outcome": "refer",
            "reason": "A handler should review the evidence.",
            "policy_section": "Section 7: Emergency Medical",
            "benefit": "emergency_medical",
            "lines": [],
            "paid_by_others": 0,
        },
    )
    api.update_run_todo(run_id, todo_id, "done")
    api.complete_run(run_id, "Reviewed one claim.")
    assert api.get_run(run_id)["status"] == "completed"
    latest_run = api.get_latest_run()
    assert latest_run is not None
    assert latest_run["run_id"] == run_id

    named_run_id = api.create_direct_claim_run("CLM-1004")
    api.interrupt_run(named_run_id)
    assert api.get_run(named_run_id)["status"] == "interrupted"


def test_checkpointer_uses_memory_without_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with checkpointer() as saver:
        assert isinstance(saver, InMemorySaver)


def test_run_queue_carries_saved_work_before_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(api, "get_latest_run", lambda: None)
    monkeypatch.setattr(
        api,
        "get_queue_claims",
        lambda: [{"claim_id": "CLM-1001", "status": "in progress"}],
    )
    monkeypatch.setattr("agent.harness.model", lambda claim_id: object())
    monkeypatch.setattr("agent.harness.build_claim_graph", lambda llm, saver: object())
    monkeypatch.setattr(
        "agent.harness.carry_on", lambda claim_id, graph: "recovered claim"
    )

    assert run_queue(1) == "No claims are waiting"
    assert capsys.readouterr().out.strip() == "recovered claim"
