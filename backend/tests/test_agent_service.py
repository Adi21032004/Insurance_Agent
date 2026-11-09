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
    agent_service.start_claim("CLM-1001")
    agent_service.resume_claim("CLM-1001")

    assert requests == ["/api/claims/CLM-1001/run", "/api/claims/CLM-1001/resume"]


def test_agent_service_preserves_conflict_responses(
    monkeypatch: pytest.MonkeyPatch,
):
    response = httpx.Response(
        httpx.codes.CONFLICT,
        json={"detail": "A dispatch or an evaluation is already running"},
        request=httpx.Request("POST", "http://agent.local/api/dispatches"),
    )

    def reject_start(path: str, json_body=None) -> httpx.Response:
        raise httpx.HTTPStatusError(
            "conflict", request=response.request, response=response
        )

    monkeypatch.setattr(agent_service, "post_agent", reject_start)

    with pytest.raises(
        agent_service.AgentServiceConflict,
        match="A dispatch or an evaluation is already running",
    ):
        agent_service.start_dispatch(3)


def test_agent_service_logs_unavailable_service(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def fail_request(path: str, json_body=None) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(agent_service, "post_agent", fail_request)

    with pytest.raises(RuntimeError, match="The agent's service isn't running"):
        agent_service.start_claim("CLM-1002")
    agent_service.resume_claim("CLM-1002")

    assert "Could not resume signed-off claim CLM-1002" in caplog.text
