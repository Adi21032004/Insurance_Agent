import threading
import time

from langchain_core.exceptions import ModelTimeoutError

from agent import dispatcher


def test_dispatch_runs_claim_agents_in_parallel_up_to_the_configured_limit(
    monkeypatch,
):
    monkeypatch.setattr(dispatcher.api, "finish_run", lambda run_id: None)
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    completed: list[str] = []

    def work(claim_id: str, run_id: str) -> str:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
            completed.append(claim_id)
        return f"{claim_id}:{run_id}"

    reports = dispatcher.work_run(
        {
            "run_id": "dispatch-1",
            "agents": 3,
            "claim_ids": [f"CLM-{claim:04d}" for claim in range(1, 7)],
        },
        work,
    )

    assert maximum_active == 3
    assert len(completed) == 6
    assert set(reports) == {f"{claim_id}:dispatch-1" for claim_id in completed}


def test_dispatch_skips_claims_currently_resuming(monkeypatch):
    monkeypatch.setattr(dispatcher.api, "finish_run", lambda run_id: None)
    worked: list[str] = []

    reports = dispatcher.work_run(
        {
            "run_id": "dispatch-2",
            "agents": 2,
            "claim_ids": ["CLM-1001", "CLM-1002"],
        },
        lambda claim_id, run_id: worked.append(claim_id) or claim_id,
        resuming={"CLM-1001"},
    )

    assert worked == ["CLM-1002"]
    assert reports == ["CLM-1002"]


def test_dispatch_retries_transient_errors_without_aborting_other_claims(
    monkeypatch,
):
    monkeypatch.setattr(dispatcher.api, "finish_run", lambda run_id: None)
    monkeypatch.setattr(
        dispatcher,
        "RETRY",
        dispatcher.RETRY._replace(initial_interval=0, jitter=False),
    )
    attempts: dict[str, int] = {}

    def work(claim_id: str, run_id: str) -> str:
        attempts[claim_id] = attempts.get(claim_id, 0) + 1
        if claim_id == "CLM-1001" and attempts[claim_id] < 3:
            raise ModelTimeoutError("temporary timeout")
        return claim_id

    reports = dispatcher.work_run(
        {
            "run_id": "dispatch-retry",
            "agents": 2,
            "claim_ids": ["CLM-1001", "CLM-1002"],
        },
        work,
    )

    assert attempts == {"CLM-1001": 3, "CLM-1002": 1}
    assert set(reports) == {"CLM-1001", "CLM-1002"}


def test_dispatch_retries_other_errors_once_and_keeps_finishing(
    monkeypatch,
):
    monkeypatch.setattr(dispatcher.api, "finish_run", lambda run_id: None)
    attempts: dict[str, int] = {}

    def work(claim_id: str, run_id: str) -> str:
        attempts[claim_id] = attempts.get(claim_id, 0) + 1
        if claim_id == "CLM-1001":
            raise ValueError("provider rejected the request")
        return claim_id

    reports = dispatcher.work_run(
        {
            "run_id": "dispatch-failure",
            "agents": 2,
            "claim_ids": ["CLM-1001", "CLM-1002"],
        },
        work,
    )

    assert attempts == {"CLM-1001": 2, "CLM-1002": 1}
    assert any("CLM-1001  left for the next dispatch" in report for report in reports)
    assert "CLM-1002" in reports
