import json

from backend import database


def test_initialize_database_seeds_only_the_queue(monkeypatch, tmp_path):
    path = tmp_path / "claims.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", path)

    database.initialize_database()
    records = database.list_claim_records()

    assert len(records) == 25
    assert {record["status"] for record in records} == {"waiting"}
    assert records[0]["claim"]["amount_claimed"] == 420.0
    assert records[0]["decision"] is None
    assert path.exists()


def test_initialize_database_does_not_duplicate_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")

    database.initialize_database()
    database.initialize_database()

    assert len(database.list_claim_records()) == 25


def test_database_seed_sources_are_explicit():
    source = (database.ROOT / "backend" / "database.py").read_text(encoding="utf-8")
    assert 'read_json("claims.json")' in source
    assert 'read_json("policies.json")' in source
    assert "evals" not in source


def test_source_claims_remain_available_in_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "claims.sqlite3")
    database.initialize_database()
    record = database.get_claim_record("CLM-1001")

    assert record is not None
    assert json.loads(json.dumps(record["claim"]))["amount_claimed"] == 420.0
