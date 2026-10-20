"""SQLite storage for the claims pilot."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATABASE_PATH = Path(os.getenv("UNDERCOVER_DB_PATH", str(ROOT / ".undercover.sqlite3")))
RecordTable = Literal["decisions", "payments", "signoffs"]
MAX_CLAIM_FAILURES = 2
RECORD_SQL = {
    "decisions": {
        "select": "SELECT rowid AS migration_rowid, claim_id, decision_json, "
        "idempotency_key FROM decisions ORDER BY decision_id",
        "delete": "DELETE FROM decisions WHERE rowid = ?",
        "update": "UPDATE decisions SET idempotency_key = ? WHERE rowid = ?",
    },
    "payments": {
        "select": "SELECT rowid AS migration_rowid, claim_id, payment_json, "
        "idempotency_key FROM payments ORDER BY payment_id",
        "delete": "DELETE FROM payments WHERE rowid = ?",
        "update": "UPDATE payments SET idempotency_key = ? WHERE rowid = ?",
    },
    "signoffs": {
        "select": "SELECT rowid AS migration_rowid, claim_id, handler_id, "
        "idempotency_key FROM signoffs ORDER BY signoff_id",
        "delete": "DELETE FROM signoffs WHERE rowid = ?",
        "update": "UPDATE signoffs SET idempotency_key = ? WHERE rowid = ?",
    },
}


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Open a transaction on the pilot database."""
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def touch_claim_lease(db: sqlite3.Connection, claim_id: str) -> None:
    """Refresh the run and claim heartbeat while claim work is active."""
    db.execute(
        "UPDATE claims SET lease_at = CURRENT_TIMESTAMP "
        "WHERE claim_id = ? AND run_id IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM runs WHERE runs.run_id = claims.run_id "
        "AND runs.status = 'in progress')",
        (claim_id,),
    )
    db.execute(
        "UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE run_id = "
        "(SELECT run_id FROM claims WHERE claim_id = ?)",
        (claim_id,),
    )


def touch_run(db: sqlite3.Connection, run_id: str) -> None:
    """Refresh one persisted run heartbeat."""
    db.execute(
        "UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE run_id = ?",
        (run_id,),
    )


def read_json(filename: str) -> Any:
    """Read one of the approved pilot seed files."""
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def make_idempotency_key(kind: str, claim_id: str, value: Any) -> str:
    """Build a stable idempotency key from one claim write."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{kind}:{claim_id}:{digest}"


def migrate_unique_records(db: sqlite3.Connection, table: RecordTable) -> None:
    """Deduplicate legacy claim writes and log rows removed by migration."""
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if "idempotency_key" not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN idempotency_key TEXT")
    rows = db.execute(RECORD_SQL[table]["select"]).fetchall()
    value_column = {
        "decisions": "decision_json",
        "payments": "payment_json",
        "signoffs": "handler_id",
    }[table]
    seen_claims: set[str] = set()
    for row in rows:
        claim_id = row["claim_id"]
        if claim_id in seen_claims:
            claim = db.execute(
                "SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if claim is not None:
                db.execute(
                    "INSERT INTO events (claim_id, step, detail) VALUES (?, ?, ?)",
                    (
                        claim_id,
                        "Duplicate record removed",
                        "A duplicate "
                        f"{table[:-1]} row was removed during database upgrade.",
                    ),
                )
            db.execute(RECORD_SQL[table]["delete"], (row["migration_rowid"],))
            continue
        seen_claims.add(claim_id)
        value = row[value_column]
        key = row["idempotency_key"] or make_idempotency_key(
            table[:-1], claim_id, value
        )
        db.execute(RECORD_SQL[table]["update"], (key, row["migration_rowid"]))


def backfill_claim_records(db: sqlite3.Connection) -> None:
    """Copy pilot JSON decisions, sign-offs and payments into unique tables."""
    rows = db.execute(
        "SELECT claim_id, decision_json, sign_off_handler_id, payment_json FROM claims"
    ).fetchall()
    for row in rows:
        claim_id = row["claim_id"]
        if (
            row["decision_json"] is not None
            and db.execute(
                "SELECT 1 FROM decisions WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            is None
        ):
            db.execute(
                "INSERT INTO decisions "
                "(claim_id, decision_json, idempotency_key) VALUES (?, ?, ?)",
                (
                    claim_id,
                    row["decision_json"],
                    make_idempotency_key(
                        "decision", claim_id, json.loads(row["decision_json"])
                    ),
                ),
            )
        if (
            row["payment_json"] is not None
            and db.execute(
                "SELECT 1 FROM payments WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            is None
        ):
            db.execute(
                "INSERT INTO payments "
                "(claim_id, payment_json, idempotency_key) VALUES (?, ?, ?)",
                (
                    claim_id,
                    row["payment_json"],
                    make_idempotency_key(
                        "payment", claim_id, json.loads(row["payment_json"])
                    ),
                ),
            )
        if (
            row["sign_off_handler_id"] is not None
            and db.execute(
                "SELECT 1 FROM signoffs WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            is None
        ):
            db.execute(
                "INSERT INTO signoffs (claim_id, handler_id, idempotency_key) "
                "VALUES (?, ?, ?)",
                (
                    claim_id,
                    row["sign_off_handler_id"],
                    make_idempotency_key(
                        "signoff", claim_id, row["sign_off_handler_id"]
                    ),
                ),
            )
    decisions = db.execute("SELECT claim_id, decision_json FROM decisions").fetchall()
    for record in decisions:
        db.execute(
            "UPDATE claims SET decision_json = ? WHERE claim_id = ?",
            (record["decision_json"], record["claim_id"]),
        )
    payments = db.execute("SELECT claim_id, payment_json FROM payments").fetchall()
    for record in payments:
        db.execute(
            "UPDATE claims SET payment_json = ? WHERE claim_id = ?",
            (record["payment_json"], record["claim_id"]),
        )
    signoffs = db.execute("SELECT claim_id, handler_id FROM signoffs").fetchall()
    for record in signoffs:
        db.execute(
            "UPDATE claims SET sign_off_handler_id = ? WHERE claim_id = ?",
            (record["handler_id"], record["claim_id"]),
        )


def ensure_workflow_columns(db: sqlite3.Connection) -> None:
    """Add run, lease and retry fields to pilot databases."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    for name, declaration in (
        ("failure_count", "INTEGER NOT NULL DEFAULT 0"),
        ("run_id", "TEXT"),
        ("lease_at", "TEXT"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE claims ADD COLUMN {name} {declaration}")
    run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
    for name, declaration in (
        ("selection_complete", "INTEGER NOT NULL DEFAULT 0"),
        ("required_claims", "INTEGER"),
        ("heartbeat_at", "TEXT"),
    ):
        if name not in run_columns:
            db.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
    todo_columns = {
        row[1] for row in db.execute("PRAGMA table_info(run_todos)").fetchall()
    }
    if "attempt_count" not in todo_columns:
        db.execute(
            "ALTER TABLE run_todos ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
        )


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
        "claims_manual": (DATA_DIR / "claims_manual.md").read_text(encoding="utf-8"),
    }

    with connection() as db:
        db.executescript(
            """
            BEGIN IMMEDIATE;
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
                payment_json TEXT,
                run_id TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                lease_at TEXT
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
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                run_type TEXT NOT NULL,
                max_claims INTEGER NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                selection_complete INTEGER NOT NULL DEFAULT 0,
                required_claims INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                heartbeat_at TEXT
            );
            CREATE TABLE IF NOT EXISTS run_todos (
                todo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                position INTEGER NOT NULL,
                claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'to do',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(run_id, position),
                UNIQUE(run_id, claim_id)
            );
            CREATE TABLE IF NOT EXISTS source_reads (
                read_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL REFERENCES claims(claim_id),
                source TEXT NOT NULL,
                section_id TEXT NOT NULL,
                heading TEXT NOT NULL,
                version TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(claim_id, source, section_id)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                payment_json TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS signoffs (
                signoff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                handler_id TEXT NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        ensure_workflow_columns(db)
        count = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        if not count:
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

        for name, defaults in settings.items():
            row = db.execute(
                "SELECT value FROM settings WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO settings (name, value) VALUES (?, ?)",
                    (name, json.dumps(defaults)),
                )
                continue

            existing = json.loads(row["value"])
            if isinstance(defaults, dict) and isinstance(existing, dict):
                merged = {**defaults, **existing}
                if merged != existing:
                    db.execute(
                        "UPDATE settings SET value = ? WHERE name = ?",
                        (json.dumps(merged), name),
                    )

        migrate_unique_records(db, "decisions")
        migrate_unique_records(db, "payments")
        migrate_unique_records(db, "signoffs")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS decisions_claim_unique "
            "ON decisions(claim_id)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS decisions_key_unique "
            "ON decisions(idempotency_key)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS payments_claim_unique "
            "ON payments(claim_id)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS payments_key_unique "
            "ON payments(idempotency_key)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS signoffs_claim_unique "
            "ON signoffs(claim_id)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS signoffs_key_unique "
            "ON signoffs(idempotency_key)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_active_run "
            "ON runs(status) WHERE status = 'in progress'"
        )
        backfill_claim_records(db)


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
        "run_id": row["run_id"],
        "failure_count": row["failure_count"],
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


def record_source_read(claim_id: str, section: dict[str, str]) -> None:
    """Persist the first read of a knowledge section for a claim."""
    with connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO source_reads "
            "(claim_id, source, section_id, heading, version, content) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                claim_id,
                section["source"],
                section["section_id"],
                section["heading"],
                section["version"],
                section["content"],
            ),
        )
        touch_claim_lease(db, claim_id)


def list_source_reads(claim_id: str) -> list[dict[str, Any]]:
    """Return the policy and manual sections read for one claim."""
    with connection() as db:
        rows = db.execute(
            "SELECT source, section_id, heading, version, content, created_at "
            "FROM source_reads WHERE claim_id = ? ORDER BY read_id",
            (claim_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_claim_status(claim_id: str, status: str) -> None:
    """Set the queue status for one claim."""
    with connection() as db:
        result = db.execute(
            "UPDATE claims SET status = ?, lease_at = CASE "
            "WHEN ? = 'in progress' THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE claim_id = ?",
            (status, status, claim_id),
        )
    if result.rowcount == 0:
        raise KeyError(claim_id)


def add_event(claim_id: str, step: str, detail: str) -> None:
    """Persist one claim progress event."""
    with connection() as db:
        db.execute(
            "INSERT INTO events (claim_id, step, detail) VALUES (?, ?, ?)",
            (claim_id, step, detail),
        )
        touch_claim_lease(db, claim_id)


def save_decision(claim_id: str, decision: dict[str, Any], status: str) -> None:
    """Persist one idempotent policy decision and its queue status."""
    decision_json = json.dumps(decision)
    key = make_idempotency_key("decision", claim_id, decision)
    with connection() as db:
        existing = db.execute(
            "SELECT idempotency_key FROM decisions WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing is not None:
            if existing["idempotency_key"] == key:
                return
            raise ValueError("Claim already has a different decision")
        result = db.execute(
            "UPDATE claims SET decision_json = ?, status = ?, "
            "lease_at = CURRENT_TIMESTAMP "
            "WHERE claim_id = ? AND decision_json IS NULL",
            (decision_json, status, claim_id),
        )
        if result.rowcount == 0:
            raise ValueError("Claim not found or already decided")
        db.execute(
            "INSERT INTO decisions (claim_id, decision_json, idempotency_key) "
            "VALUES (?, ?, ?)",
            (claim_id, decision_json, key),
        )
        touch_claim_lease(db, claim_id)


def sign_off_claim(claim_id: str, handler_id: str) -> None:
    """Record one idempotent handler sign-off."""
    key = make_idempotency_key("signoff", claim_id, handler_id)
    with connection() as db:
        existing = db.execute(
            "SELECT idempotency_key FROM signoffs WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing is not None:
            if existing["idempotency_key"] == key:
                return
            raise ValueError("Claim already has a different handler sign-off")
        result = db.execute(
            "UPDATE claims SET sign_off_handler_id = ?, status = 'decided' "
            "WHERE claim_id = ? AND status = 'waiting for sign-off' "
            "AND sign_off_handler_id IS NULL",
            (handler_id, claim_id),
        )
        if result.rowcount == 0:
            raise ValueError("Claim is not waiting for sign-off")
        db.execute(
            "INSERT INTO signoffs (claim_id, handler_id, idempotency_key) "
            "VALUES (?, ?, ?)",
            (claim_id, handler_id, key),
        )


def save_payment(claim_id: str, amount_inr: float) -> None:
    """Record one idempotent simulated INR payment."""
    payment = {"amount_inr": amount_inr, "simulated": True}
    payment_json = json.dumps(payment)
    key = make_idempotency_key("payment", claim_id, payment)
    with connection() as db:
        existing = db.execute(
            "SELECT idempotency_key FROM payments WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing is not None:
            if existing["idempotency_key"] == key:
                return
            raise ValueError("Claim already has a different payment")
        result = db.execute(
            "UPDATE claims SET payment_json = ?, status = 'decided', "
            "failure_count = 0, lease_at = NULL "
            "WHERE claim_id = ? "
            "AND payment_json IS NULL",
            (payment_json, claim_id),
        )
        if result.rowcount == 0:
            raise ValueError("Claim not found or already paid")
        db.execute(
            "INSERT INTO payments (claim_id, payment_json, idempotency_key) "
            "VALUES (?, ?, ?)",
            (claim_id, payment_json, key),
        )


def create_run(run_id: str, run_type: str, max_claims: int) -> None:
    """Create a persisted queue or named-claim run."""
    with connection() as db:
        try:
            db.execute(
                "INSERT INTO runs (run_id, run_type, max_claims, status, heartbeat_at) "
                "VALUES (?, ?, ?, 'in progress', CURRENT_TIMESTAMP)",
                (run_id, run_type, max_claims),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("Another agent run is already active") from error


def list_waiting_claim_ids(limit: int) -> list[str]:
    """Return up to the requested number of waiting claim identifiers."""
    with connection() as db:
        rows = db.execute(
            "SELECT claim_id FROM claims WHERE status = 'waiting' "
            "ORDER BY claim_id LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["claim_id"] for row in rows]


def validate_selection_request(
    db: sqlite3.Connection, run_id: str, claim_ids: list[str]
) -> None:
    """Reject invalid claim selection before making any reservation."""
    run = db.execute(
        "SELECT run_type, max_claims, status, selection_complete "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise KeyError(run_id)
    if run["status"] != "in progress":
        raise ValueError("Run is not active")
    if run["selection_complete"]:
        raise ValueError("Claims have already been selected for this run")
    if len(claim_ids) > run["max_claims"]:
        raise ValueError("Selected claims exceed the run cap")
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("A claim can only be selected once")
    if run["run_type"] == "queue":
        waiting_count = db.execute(
            "SELECT COUNT(*) FROM claims WHERE status = 'waiting'"
        ).fetchone()[0]
        expected = min(run["max_claims"], waiting_count)
        if len(claim_ids) != expected:
            raise ValueError(f"Run must select exactly {expected} waiting claims")


def validate_claims_waiting(db: sqlite3.Connection, claim_ids: list[str]) -> None:
    """Confirm every requested claim is still waiting before reservation."""
    for claim_id in claim_ids:
        claim = db.execute(
            "SELECT status FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise KeyError(claim_id)
        if claim["status"] != "waiting":
            raise ValueError(f"Claim {claim_id} is not waiting")


def select_run_claims(run_id: str, claim_ids: list[str]) -> None:
    """Atomically reserve selected waiting claims and create their to-dos."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        validate_selection_request(db, run_id, claim_ids)
        validate_claims_waiting(db, claim_ids)
        for position, claim_id in enumerate(claim_ids):
            updated = db.execute(
                "UPDATE claims SET status = 'in progress', run_id = ?, "
                "lease_at = CURRENT_TIMESTAMP "
                "WHERE claim_id = ? AND status = 'waiting'",
                (run_id, claim_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Claim {claim_id} was acquired by another run")
            db.execute(
                "INSERT INTO run_todos "
                "(run_id, position, claim_id, description) VALUES (?, ?, ?, ?)",
                (run_id, position, claim_id, f"Review {claim_id}"),
            )
        db.execute(
            "UPDATE runs SET selection_complete = 1, required_claims = ?, "
            "heartbeat_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (len(claim_ids), run_id),
        )


def plan_run_todos(run_id: str, items: list[dict[str, str]]) -> None:
    """Persist the queue agent's order and descriptions for selected claims."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        if run["status"] != "in progress":
            raise ValueError("Run is not active")
        current = db.execute(
            "SELECT todo_id, position, claim_id, status, attempt_count FROM run_todos "
            "WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
        requested_ids = [item["claim_id"] for item in items]
        if len(set(requested_ids)) != len(requested_ids) or set(requested_ids) != {
            row["claim_id"] for row in current
        }:
            raise ValueError("Run plan must include each selected claim once")
        offset = len(current)
        db.execute(
            "UPDATE run_todos SET position = position + ? WHERE run_id = ?",
            (offset, run_id),
        )
        for position, item in enumerate(items):
            db.execute(
                "UPDATE run_todos SET position = ?, description = ? "
                "WHERE run_id = ? AND claim_id = ?",
                (position, item["description"], run_id, item["claim_id"]),
            )
        touch_run(db, run_id)


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return one run and its ordered work items."""
    with connection() as db:
        row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        todos = db.execute(
            "SELECT todo_id, position, claim_id, description, status, attempt_count "
            "FROM run_todos "
            "WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
    return {**dict(row), "todos": [dict(todo) for todo in todos]}


def get_current_run() -> dict[str, Any] | None:
    """Return the active run and its work items, if one exists."""
    with connection() as db:
        row = db.execute(
            "SELECT run_id FROM runs WHERE status = 'in progress' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return get_run(row["run_id"]) if row is not None else None


def set_todo_status(todo_id: int, status: str) -> None:
    """Update the state of one persisted work item."""
    with connection() as db:
        todo = db.execute(
            "SELECT run_id, claim_id, status FROM run_todos WHERE todo_id = ?",
            (todo_id,),
        ).fetchone()
        if todo is None:
            raise KeyError(todo_id)
        db.execute(
            "UPDATE run_todos SET status = ?, attempt_count = attempt_count + ? "
            "WHERE todo_id = ?",
            (
                status,
                int(status == "in progress" and todo["status"] != "in progress"),
                todo_id,
            ),
        )
        touch_run(db, todo["run_id"])
        if status == "in progress":
            touch_claim_lease(db, todo["claim_id"])
        elif status == "done":
            db.execute(
                "UPDATE claims SET failure_count = 0, lease_at = NULL "
                "WHERE claim_id = ? AND decision_json IS NOT NULL",
                (todo["claim_id"],),
            )
        elif status == "left for handler":
            db.execute(
                "UPDATE claims SET status = 'needs handler', lease_at = NULL "
                "WHERE claim_id = ?",
                (todo["claim_id"],),
            )


def set_todo_status_for_claim(run_id: str, claim_id: str, status: str) -> None:
    """Update the work item for one claim in a run."""
    with connection() as db:
        db.execute(
            "UPDATE run_todos SET status = ? WHERE run_id = ? AND claim_id = ?",
            (status, run_id, claim_id),
        )


def finish_run(run_id: str, summary: str) -> None:
    """Store a run summary and mark the run complete."""
    with connection() as db:
        db.execute(
            "UPDATE runs SET status = 'completed', summary = ?, "
            "finished_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP "
            "WHERE run_id = ? AND status = 'in progress'",
            (summary, run_id),
        )


def interrupt_run(run_id: str) -> None:
    """Mark a run interrupted after its selected claims could not be acquired."""
    with connection() as db:
        db.execute(
            "UPDATE runs SET status = 'interrupted', "
            "finished_at = CURRENT_TIMESTAMP, "
            "summary = COALESCE(summary, 'Run interrupted before completion.'), "
            "heartbeat_at = CURRENT_TIMESTAMP WHERE run_id = ? "
            "AND status = 'in progress'",
            (run_id,),
        )


def fail_claim_attempt(run_id: str, claim_id: str, detail: str) -> dict[str, Any]:
    """Persist one failed attempt and resolve its retry or handler state."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        todo = db.execute(
            "SELECT todo_id FROM run_todos WHERE run_id = ? AND claim_id = ?",
            (run_id, claim_id),
        ).fetchone()
        claim = db.execute(
            "SELECT decision_json, payment_json, sign_off_handler_id, failure_count "
            "FROM claims WHERE claim_id = ? "
            "AND run_id = ?",
            (claim_id, run_id),
        ).fetchone()
        run = db.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if todo is None or claim is None:
            raise KeyError(claim_id)
        if run is None or run["status"] != "in progress":
            raise ValueError("Run is not active")
        decision = (
            json.loads(claim["decision_json"]) if claim["decision_json"] else None
        )
        payment = json.loads(claim["payment_json"]) if claim["payment_json"] else None
        awaiting_signoff = bool(
            decision
            and decision["outcome"] == "approve"
            and decision["needs_sign_off"]
            and claim["sign_off_handler_id"] is None
            and payment is None
        )
        already_complete = bool(
            decision
            and (
                decision["outcome"] != "approve"
                or payment is not None
                or awaiting_signoff
            )
        )
        if already_complete:
            claim_status = "waiting for sign-off" if awaiting_signoff else "decided"
            db.execute(
                "UPDATE claims SET status = ?, failure_count = 0, lease_at = NULL "
                "WHERE claim_id = ?",
                (claim_status, claim_id),
            )
            db.execute(
                "UPDATE run_todos SET status = 'done' WHERE todo_id = ?",
                (todo["todo_id"],),
            )
            touch_run(db, run_id)
            return {
                "failure_count": 0,
                "retry": False,
                "already_complete": True,
                "claim_status": claim_status,
                "todo_status": "done",
            }
        failures = int(claim["failure_count"]) + 1
        retry = failures < MAX_CLAIM_FAILURES
        if not retry:
            status = "needs handler"
            todo_status = "left for handler"
        elif claim["decision_json"] is None:
            status = "waiting"
            todo_status = "to do"
        else:
            status = "in progress"
            todo_status = "to do"
        db.execute(
            "UPDATE claims SET failure_count = ?, status = ?, lease_at = "
            "CASE WHEN ? = 'in progress' THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE claim_id = ?",
            (failures, status, status, claim_id),
        )
        db.execute(
            "UPDATE run_todos SET status = ? WHERE todo_id = ?",
            (todo_status, todo["todo_id"]),
        )
        db.execute(
            "INSERT INTO events (claim_id, step, detail) VALUES (?, ?, ?)",
            (claim_id, "Attempt failed", f"Attempt {failures}: {detail}"),
        )
        touch_run(db, run_id)
        return {
            "failure_count": failures,
            "retry": retry,
            "claim_status": status,
            "todo_status": todo_status,
        }


def retry_claim_attempt(run_id: str, claim_id: str) -> int:
    """Reacquire one claim for a retry in its existing run."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        todo = db.execute(
            "SELECT todo_id, status, attempt_count FROM run_todos "
            "WHERE run_id = ? AND claim_id = ?",
            (run_id, claim_id),
        ).fetchone()
        claim = db.execute(
            "SELECT status, failure_count, decision_json "
            "FROM claims WHERE claim_id = ? "
            "AND run_id = ?",
            (claim_id, run_id),
        ).fetchone()
        if run is None or todo is None or claim is None:
            raise KeyError(claim_id)
        if run["status"] != "in progress":
            raise ValueError("Run is not active")
        if claim["failure_count"] >= MAX_CLAIM_FAILURES:
            raise ValueError("Claim already needs handler attention")
        if todo["status"] != "to do":
            raise ValueError("Claim is not ready for retry")
        if claim["status"] == "waiting":
            updated = db.execute(
                "UPDATE claims SET status = 'in progress', lease_at = "
                "CURRENT_TIMESTAMP WHERE claim_id = ? AND status = 'waiting' "
                "AND run_id = ?",
                (claim_id, run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Claim could not be reacquired for retry")
        elif claim["status"] != "in progress" or claim["decision_json"] is None:
            raise ValueError("Claim state is not retryable")
        db.execute(
            "UPDATE run_todos SET status = 'in progress', "
            "attempt_count = attempt_count + 1 WHERE todo_id = ?",
            (todo["todo_id"],),
        )
        db.execute(
            "INSERT INTO events (claim_id, step, detail) VALUES (?, ?, ?)",
            (claim_id, "Attempt started", f"Attempt {int(todo['attempt_count']) + 1}."),
        )
        touch_run(db, run_id)
        return int(todo["attempt_count"]) + 1


def expired_run_ids(lease_seconds: int) -> list[str]:
    """List active runs whose heartbeat lease has expired."""
    with connection() as db:
        rows = db.execute(
            "SELECT run_id FROM runs WHERE status = 'in progress' AND "
            "COALESCE(heartbeat_at, created_at) <= datetime('now', ?) "
            "ORDER BY created_at",
            (f"-{lease_seconds} seconds",),
        ).fetchall()
    return [row["run_id"] for row in rows]


def recover_expired_todo(
    db: sqlite3.Connection, run_id: str, todo: sqlite3.Row
) -> bool:
    """Resolve persisted claim state and report whether it needs continuation."""
    decision = json.loads(todo["decision_json"]) if todo["decision_json"] else None
    payment = json.loads(todo["payment_json"]) if todo["payment_json"] else None
    needs_handler_signoff = bool(
        decision
        and decision["outcome"] == "approve"
        and decision["needs_sign_off"]
        and todo["sign_off_handler_id"] is None
        and payment is None
    )
    already_complete = bool(
        decision
        and (
            decision["outcome"] != "approve"
            or payment is not None
            or needs_handler_signoff
        )
    )
    if already_complete:
        status = "waiting for sign-off" if needs_handler_signoff else "decided"
        db.execute(
            "UPDATE claims SET status = ?, failure_count = 0, lease_at = NULL "
            "WHERE claim_id = ?",
            (status, todo["claim_id"]),
        )
        db.execute(
            "UPDATE run_todos SET status = 'done' WHERE todo_id = ?",
            (todo["todo_id"],),
        )
        return False

    waiting_for_retry = todo["claim_status"] == "waiting" and todo["status"] == "to do"
    needs_payment_resume = bool(
        decision
        and decision["outcome"] == "approve"
        and payment is None
        and not needs_handler_signoff
    )
    stuck = todo["claim_status"] == "in progress" or needs_payment_resume
    if not waiting_for_retry and not stuck:
        if todo["claim_status"] == "needs handler":
            db.execute(
                "UPDATE run_todos SET status = 'left for handler' WHERE todo_id = ?",
                (todo["todo_id"],),
            )
        return False

    failures = int(todo["failure_count"])
    if stuck:
        failures += 1
        db.execute(
            "INSERT INTO events (claim_id, step, detail) "
            "VALUES (?, 'Attempt interrupted', ?)",
            (todo["claim_id"], f"Run {run_id} lease expired."),
        )
    if failures >= MAX_CLAIM_FAILURES:
        db.execute(
            "UPDATE claims SET failure_count = ?, status = 'needs handler', "
            "lease_at = NULL WHERE claim_id = ?",
            (failures, todo["claim_id"]),
        )
        db.execute(
            "UPDATE run_todos SET status = 'left for handler' WHERE todo_id = ?",
            (todo["todo_id"],),
        )
        return False

    db.execute(
        "UPDATE claims SET failure_count = ?, status = 'waiting', lease_at = NULL "
        "WHERE claim_id = ?",
        (failures, todo["claim_id"]),
    )
    db.execute(
        "UPDATE run_todos SET status = 'to do' WHERE todo_id = ?",
        (todo["todo_id"],),
    )
    return True


def recover_expired_run(
    run_id: str, lease_seconds: int, force: bool = False
) -> str | None:
    """Interrupt an expired run and create a continuation for recoverable claims."""
    cutoff = f"-{lease_seconds} seconds"
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT * FROM runs WHERE run_id = ? AND status = 'in progress' "
            "AND (? = 1 OR COALESCE(heartbeat_at, created_at) <= datetime('now', ?))",
            (run_id, int(force), cutoff),
        ).fetchone()
        if run is None:
            return None
        todos = db.execute(
            "SELECT t.todo_id, t.position, t.claim_id, t.description, t.status, "
            "c.status AS claim_status, c.failure_count, c.decision_json, "
            "c.payment_json, c.sign_off_handler_id, c.lease_at "
            "FROM run_todos AS t JOIN claims AS c USING (claim_id) "
            "WHERE t.run_id = ? ORDER BY t.position",
            (run_id,),
        ).fetchall()
        resumes = [todo for todo in todos if recover_expired_todo(db, run_id, todo)]

        continuation_id = str(uuid4()) if resumes else None
        summary = "Run interrupted after its heartbeat lease expired."
        if continuation_id:
            summary += f" Recoverable claims moved to run {continuation_id}."
        db.execute(
            "UPDATE runs SET status = 'interrupted', summary = ?, "
            "finished_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            (summary, run_id),
        )
        if continuation_id is not None:
            db.execute(
                "INSERT INTO runs (run_id, run_type, max_claims, status, summary, "
                "selection_complete, required_claims, heartbeat_at) "
                "VALUES (?, 'recovery', ?, 'in progress', ?, 1, ?, CURRENT_TIMESTAMP)",
                (
                    continuation_id,
                    len(resumes),
                    f"Resuming interrupted run {run_id}.",
                    len(resumes),
                ),
            )
            for position, todo in enumerate(resumes):
                db.execute(
                    "UPDATE claims SET status = 'in progress', run_id = ?, "
                    "lease_at = CURRENT_TIMESTAMP WHERE claim_id = ?",
                    (continuation_id, todo["claim_id"]),
                )
                db.execute(
                    "INSERT INTO run_todos "
                    "(run_id, position, claim_id, description) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        continuation_id,
                        position,
                        todo["claim_id"],
                        todo["description"],
                    ),
                )
        return continuation_id
