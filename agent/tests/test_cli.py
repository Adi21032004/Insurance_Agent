import sys
from contextlib import nullcontext

import pytest

from agent import __main__ as cli


def test_cli_runs_a_capped_queue_without_loading_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    calls: list[tuple[int, str, str]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "checkpointer", lambda: nullcontext("saver"))
    monkeypatch.setattr(cli, "model", lambda: "model")
    monkeypatch.setattr(
        cli,
        "run_queue",
        lambda cap, llm, saver: calls.append((cap, llm, saver)) or "queue done",
    )
    monkeypatch.setattr(sys, "argv", ["agent", "--cap", "3"])

    cli.main()

    assert calls == [(3, "model", "saver")]
    assert capsys.readouterr().out.strip() == "queue done"


def test_cli_runs_one_named_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "checkpointer", lambda: nullcontext("saver"))
    monkeypatch.setattr(
        cli,
        "run_claim_direct",
        lambda claim_id, saver: calls.append((claim_id, saver)) or "claim done",
    )
    monkeypatch.setattr(sys, "argv", ["agent", "CLM-1001"])

    cli.main()

    assert calls == [("CLM-1001", "saver")]
    assert capsys.readouterr().out.strip() == "claim done"
