import httpx
import pytest

from backend import agent_service


def test_agent_service_uses_configured_address_and_routes(
    monkeypatch: pytest.MonkeyPatch,
):
    requests: list[str] = []
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.local")
    monkeypatch.setattr(agent_service, "post_agent", requests.append)

    assert agent_service.agent_url() == "http://agent.local"
    agent_service.run_queue("run-1")
    agent_service.resume_claim("CLM-1001")

    assert requests == ["/api/runs/run-1/run", "/api/claims/CLM-1001/resume"]


def test_agent_service_logs_unavailable_service(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def fail_request(path: str) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(agent_service, "post_agent", fail_request)

    agent_service.run_queue("run-2")
    agent_service.resume_claim("CLM-1002")

    assert "Could not start agent run run-2" in caplog.text
    assert "Could not resume signed-off claim CLM-1002" in caplog.text
