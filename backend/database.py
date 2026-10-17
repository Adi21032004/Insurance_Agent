"""SQLite storage for the claims pilot."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATABASE_PATH = Path(os.getenv("UNDERCOVER_DB_PATH", str(ROOT / ".undercover.sqlite3")))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Open a transaction on the pilot database."""
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read_json(filename: str) -> Any:
    """Read one of the approved pilot seed files."""
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def initialize_database() -> None:
    """Create the schema and seed the claims queue once."""
    claims = read_json("claims.json")
    policies = {row["policy_id"]: row for row in read_json("policies.json")}
    customers = {row["customer_id"]: row for row in read_json("customers.json")}
    settings = {
        "plans": read_json("plans.json"),
        "handlers": read_json("handlers.json"),
        "company": read_json("company.json"),
        "policy_wording": (DATA_DIR / "policy_wording.md").read_text(encoding="utf-8"),
    }

    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                policy_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                source_json TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                customer_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                decision_json TEXT,
                sign_off_handler_id TEXT,
                payment_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                step TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        count = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        if count:
            return
        for claim in claims:
            policy = policies[claim["policy_id"]]
            customer = customers[claim["customer_id"]]
            db.execute(
                """
                INSERT INTO claims (
                    claim_id, policy_id, customer_id, source_json,
                    policy_json, customer_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    claim["claim_id"],
                    claim["policy_id"],
                    claim["customer_id"],
                    json.dumps(claim),
                    json.dumps(policy),
                    json.dumps(customer),
                ),
            )
        db.executemany(
            "INSERT INTO settings (name, value) VALUES (?, ?)",
            [(name, json.dumps(value)) for name, value in settings.items()],
        )


def list_claim_records() -> list[dict[str, Any]]:
    """Return source claim records with their current workflow state."""
    with connection() as db:
        rows = db.execute("SELECT * FROM claims ORDER BY claim_id").fetchall()
    return [claim_record(row) for row in rows]


def get_claim_record(claim_id: str) -> dict[str, Any] | None:
    """Return one source claim record and its current workflow state."""
    with connection() as db:
        row = db.execute(
            "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
    return claim_record(row) if row is not None else None


def claim_record(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite claim row into a Python record."""
    return {
        "claim": json.loads(row["source_json"]),
        "policy": json.loads(row["policy_json"]),
        "customer": json.loads(row["customer_json"]),
        "status": row["status"],
        "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
        "sign_off_handler_id": row["sign_off_handler_id"],
        "payment": json.loads(row["payment_json"]) if row["payment_json"] else None,
    }


def get_setting(name: str) -> Any:
    """Return a seeded company record or policy document."""
    with connection() as db:
        row = db.execute(
            "SELECT value FROM settings WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        raise KeyError(name)
    return json.loads(row["value"])


def list_events(claim_id: str) -> list[dict[str, Any]]:
    """Return ordered progress events for a claim."""
    with connection() as db:
        rows = db.execute(
            "SELECT step, detail, created_at FROM events "
            "WHERE claim_id = ? ORDER BY event_id",
            (claim_id,),
        ).fetchall()
    return [dict(row) for row in rows]
