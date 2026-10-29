from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import agent_service, database
from backend.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")
    monkeypatch.setattr(agent_service, "run_queue", lambda run_id: None)
    monkeypatch.setattr(agent_service, "resume_claim", lambda claim_id: None)
    database_path = tmp_path / "claims.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    with TestClient(app) as test_client:
        yield test_client
