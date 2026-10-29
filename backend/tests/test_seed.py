from backend import database, seed


def test_seed_reports_local_and_postgres_destinations(monkeypatch, capsys):
    counts = {
        "claims.json": [1, 2],
        "policies.json": [1],
        "customers.json": [1, 2, 3],
        "handlers.json": [1],
    }
    monkeypatch.setattr(database, "reseed_database", lambda: None)
    monkeypatch.setattr(database, "read_json", counts.__getitem__)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    seed.main()

    assert capsys.readouterr().out.strip() == (
        "Seeded 2 claims, 1 policies, 3 customers and 1 handlers into undercover.db"
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    seed.main()

    assert capsys.readouterr().out.strip() == (
        "Seeded 2 claims, 1 policies, 3 customers and 1 handlers into "
        "the undercover schema on Postgres"
    )
