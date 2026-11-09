import threading
import time

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
