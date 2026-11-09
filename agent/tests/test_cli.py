import sys
from contextlib import nullcontext

import pytest

from agent import __main__ as cli


def test_cli_requires_a_claim_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["agent"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    assert "the following arguments are required: claim_id" in capsys.readouterr().err


def test_cli_runs_one_named_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "checkpoint_pool", lambda: nullcontext("pool"))
    monkeypatch.setattr(
        cli,
        "run_claim_direct",
        lambda claim_id, saver: calls.append((claim_id, saver)) or "claim done",
    )
    monkeypatch.setattr(sys, "argv", ["agent", "CLM-1001"])

    cli.main()

    assert calls == [("CLM-1001", "pool")]
    assert capsys.readouterr().out.strip() == "claim done"
