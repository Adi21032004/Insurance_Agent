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
    stub_refer_model,
    tool_message,
)
from backend import database
from backend.main import app


def use_test_database(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")


def test_agent_tools_read_claim_and_policy_through_backend(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))

    claim = json.loads(main.get_claim_context.invoke({"claim_id": "CLM-1001"}))
    wording = main.get_policy_wording.invoke({})

    assert claim["claim"]["amount_claimed_inr"] == 34860.0
    assert claim["customer"]["name"] == "Maya Patel"
    assert "Trip Delay" in wording
    assert "$" not in wording


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
    assert record["payment"] == {"amount_inr": 34860.0, "simulated": True}
    assert [event["step"] for event in database.list_events("CLM-1001")] == [
        "Review claim",
        "Decision recorded",
        "Simulated payment recorded",
    ]


def test_agent_records_decline_with_policy_evidence(monkeypatch, tmp_path):
    use_test_database(monkeypatch, tmp_path)
    with TestClient(app):
        database.set_claim_status("CLM-1001", "in progress")
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))

    messages = iter(
        [
            tool_message("get_claim_context", {"claim_id": "CLM-1001"}, "1"),
            tool_message("get_policy_wording", {}, "2"),
            tool_message(
                "record_claim_progress",
                {"claim_id": "CLM-1001", "step": "Evidence", "detail": "Reviewing."},
                "3",
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
                "4",
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
    assert default_model.model_name == "openai/gpt-6-luna"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    configured_model = main.model()
    assert isinstance(configured_model, ChatOpenAI)
    assert configured_model.model_name == "openrouter/free"


def test_test_mode_model_is_scripted(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_AGENT_TEST_MODE", "1")
    monkeypatch.setenv("UNDERCOVER_TEST_CLAIM_ID", "CLM-1001")

    assert isinstance(main.model(), StubModel)
