from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database
from backend.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "claims.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    with TestClient(app) as test_client:
        yield test_client
