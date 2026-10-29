"""SQLAlchemy Core persistence for the claims pilot."""

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import Executable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATABASE_PATH = Path(
    os.getenv(
        "UNDERCOVER_DB", os.getenv("UNDERCOVER_DB_PATH", str(ROOT / "undercover.db"))
    )
)
RecordTable = Literal["decisions", "payments", "signoffs"]
MAX_CLAIM_FAILURES = 2
POSTGRES_CONNECT_ARGS = {"options": "-c search_path=undercover"}
RECORD_SQL = {
    "decisions": {
        "select": "SELECT decision_id AS record_key, claim_id, decision_json, "
        "idempotency_key FROM decisions ORDER BY decision_id",
        "delete": "DELETE FROM decisions WHERE decision_id = ?",
        "update": "UPDATE decisions SET idempotency_key = ? WHERE decision_id = ?",
    },
    "payments": {
        "select": "SELECT payment_id AS record_key, claim_id, payment_json, "
        "idempotency_key FROM payments ORDER BY payment_id",
        "delete": "DELETE FROM payments WHERE payment_id = ?",
        "update": "UPDATE payments SET idempotency_key = ? WHERE payment_id = ?",
    },
    "signoffs": {
        "select": "SELECT signoff_id AS record_key, claim_id, handler_id, "
        "idempotency_key FROM signoffs ORDER BY signoff_id",
        "delete": "DELETE FROM signoffs WHERE signoff_id = ?",
        "update": "UPDATE signoffs SET idempotency_key = ? WHERE signoff_id = ?",
    },
}
metadata = MetaData()
settings_table = Table(
    "settings",
    metadata,
    Column("name", String, primary_key=True),
    Column("value", JSON, nullable=False),
)
documents_table = Table(
    "documents",
    metadata,
    Column("name", String, primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("content", Text, nullable=False),
)
handlers_table = Table(
    "handlers",
    metadata,
    Column("handler_id", String, primary_key=True),
    Column("handler_json", JSON, nullable=False),
)
customers_table = Table(
    "customers",
    metadata,
    Column("customer_id", String, primary_key=True),
    Column("customer_json", JSON, nullable=False),
)
policies_table = Table(
    "policies",
    metadata,
    Column("policy_id", String, primary_key=True),
    Column("policy_json", JSON, nullable=False),
)
claims_table = Table(
    "claims",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("policy_id", String, ForeignKey("policies.policy_id"), nullable=False),
    Column("customer_id", String, ForeignKey("customers.customer_id"), nullable=False),
    Column("source_json", Text, nullable=False),
    Column("policy_json", Text, nullable=False),
    Column("customer_json", Text, nullable=False),
    Column("status", String, nullable=False, server_default="waiting"),
    Column("decision_json", Text),
    Column("sign_off_handler_id", String),
    Column("payment_json", Text),
    Column("run_id", String),
    Column("thread_id", String),
    Column("failure_count", Integer, nullable=False, server_default="0"),
    Column("lease_at", String),
)
steps_table = Table(
    "steps",
    metadata,
    Column("step_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, ForeignKey("claims.claim_id"), nullable=False),
    Column("step", String, nullable=False),
    Column("detail", Text, nullable=False),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
)
runs_table = Table(
    "runs",
    metadata,
    Column("run_order", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False, unique=True),
    Column("run_type", String, nullable=False),
    Column("max_claims", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("summary", Text),
    Column("selection_complete", Integer, nullable=False, server_default="0"),
    Column("required_claims", Integer),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
    Column("finished_at", String),
    Column("heartbeat_at", String),
)
todos_table = Table(
    "todos",
    metadata,
    Column("todo_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("claim_id", String, ForeignKey("claims.claim_id"), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String, nullable=False, server_default="to do"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    UniqueConstraint("run_id", "position"),
    UniqueConstraint("run_id", "claim_id"),
)
source_reads_table = Table(
    "source_reads",
    metadata,
    Column("read_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, ForeignKey("claims.claim_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("section_id", String, nullable=False),
    Column("heading", String, nullable=False),
    Column("version", String, nullable=False),
    Column("content", Text, nullable=False),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
    UniqueConstraint("claim_id", "source", "section_id"),
)
decisions_table = Table(
    "decisions",
    metadata,
    Column("decision_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("decision_json", Text, nullable=False),
    Column("idempotency_key", String),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
)
payments_table = Table(
    "payments",
    metadata,
    Column("payment_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("payment_json", Text, nullable=False),
    Column("idempotency_key", String),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
)
signoffs_table = Table(
    "signoffs",
    metadata,
    Column("signoff_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("handler_id", String, nullable=False),
    Column("idempotency_key", String),
    Column(
        "created_at", String, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
)
claim_view = (
    select(
        claims_table,
        policies_table.c.policy_json.label("normalized_policy_json"),
        customers_table.c.customer_json.label("normalized_customer_json"),
        select(signoffs_table.c.created_at)
        .where(signoffs_table.c.claim_id == claims_table.c.claim_id)
        .scalar_subquery()
        .label("sign_off_at"),
    )
    .join(policies_table, policies_table.c.policy_id == claims_table.c.policy_id)
    .join(customers_table, customers_table.c.customer_id == claims_table.c.customer_id)
)


class RowView(dict[str, Any]):
    """Mapping row with SQLite-compatible integer column access."""

    def __init__(self, row: Any):
        super().__init__(row._mapping)
        self._values = tuple(row)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class QueryResult(Protocol):
    """Result operations shared by SQL queries and schema inspection."""

    rowcount: int

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...

    def __iter__(self) -> Iterator[Any]: ...


class ResultView:
    """Result adapter for the small subset of DB-API used by the repository."""

    def __init__(self, result: Any):
        self._result = result
        self.rowcount = result.rowcount

    def fetchone(self) -> RowView | None:
        row = self._result.fetchone()
        return RowView(row) if row is not None else None

    def fetchall(self) -> list[RowView]:
        return [RowView(row) for row in self._result.fetchall()]

    def __iter__(self) -> Iterator[RowView]:
        return iter(self.fetchall())


class CoreConnection:
    """Adapt existing parameterized queries to SQLAlchemy Core connections."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def execute(
        self,
        statement: str | Executable,
        parameters: tuple[Any, ...] = (),
    ) -> QueryResult:
        if isinstance(statement, str):
            return self._execute_text(statement, parameters)
        return self._execute_clause(statement)

    def _execute_clause(self, statement: Executable) -> ResultView:
        try:
            result = self._connection.execute(statement)
        except IntegrityError as error:
            if error.orig is None:
                raise
            raise error.orig from error
        return ResultView(result)

    def _execute_text(self, statement: str, parameters: tuple[Any, ...]) -> QueryResult:
        if statement in {"PRAGMA foreign_keys = ON", "BEGIN IMMEDIATE"}:
            return ResultView(self._connection.execute(text("SELECT 1 WHERE 1 = 0")))
        pragma = re.fullmatch(r"PRAGMA table_info\((\w+)\)", statement)
        if pragma:
            columns = inspect(self._connection).get_columns(pragma.group(1))
            rows = [
                (index, column["name"], str(column["type"]), 0, None, 0)
                for index, column in enumerate(columns)
            ]
            return SequenceResult(rows)
        query = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if statement.startswith("INSERT OR IGNORE INTO"):
            query += " ON CONFLICT DO NOTHING"
        if self._connection.dialect.name == "postgresql":
            query = query.replace(
                "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
            )
        names = [f"p{index}" for index in range(len(parameters))]
        for name in names:
            query = query.replace("?", f":{name}", 1)
        binds = dict(zip(names, parameters, strict=True))
        try:
            result = self._connection.execute(text(query), binds)
        except IntegrityError as error:
            if error.orig is None:
                raise
            raise error.orig from error
        return ResultView(result)


class SequenceResult:
    """Row sequence for SQLAlchemy inspector metadata."""

    rowcount = -1

    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


_ENGINES: dict[str, Engine] = {}


def engine() -> Engine:
    """Return the configured SQLAlchemy engine with a four-connection pool."""
    database_url = os.getenv("DATABASE_URL")
    key = database_url or str(DATABASE_PATH.resolve())
    if key not in _ENGINES:
        if database_url:
            url = database_url.replace(
                "postgres://", "postgresql+psycopg://", 1
            ).replace("postgresql://", "postgresql+psycopg://", 1)
            _ENGINES[key] = create_engine(
                url,
                pool_size=4,
                max_overflow=0,
                pool_pre_ping=True,
                connect_args=POSTGRES_CONNECT_ARGS,
            )
        else:
            url = URL.create("sqlite", database=str(DATABASE_PATH.resolve()))
            _ENGINES[key] = create_engine(
                url,
                poolclass=NullPool,
                connect_args={"check_same_thread": False},
            )
    return _ENGINES[key]


@contextmanager
def connection() -> Iterator[CoreConnection]:
    """Open a SQLAlchemy Core transaction on the configured database."""
    configured_engine = engine()
    with configured_engine.begin() as db:
        yield CoreConnection(db)


def touch_claim_lease(db: CoreConnection, claim_id: str) -> None:
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


def touch_run(db: CoreConnection, run_id: str) -> None:
    """Refresh one persisted run heartbeat."""
    db.execute(
        "UPDATE runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE run_id = ?",
        (run_id,),
    )


def lease_has_expired(heartbeat_at: Any, created_at: Any, lease_seconds: int) -> bool:
    """Check a persisted run heartbeat against the configured lease."""
    value = heartbeat_at or created_at
    timestamp = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    deadline = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    return timestamp <= deadline


def read_json(filename: str) -> Any:
    """Read one of the approved pilot seed files."""
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def decode_json(value: Any) -> Any:
    """Read a JSON column from either a JSON type or an older text column."""
    return json.loads(value) if isinstance(value, str) else value


def make_idempotency_key(kind: str, claim_id: str, value: Any) -> str:
    """Build a stable idempotency key from one claim write."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{kind}:{claim_id}:{digest}"


def migrate_unique_records(db: CoreConnection, table: RecordTable) -> None:
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
                    "INSERT INTO steps (claim_id, step, detail) VALUES (?, ?, ?)",
                    (
                        claim_id,
                        "Duplicate record removed",
                        "A duplicate "
                        f"{table[:-1]} row was removed during database upgrade.",
                    ),
                )
            db.execute(RECORD_SQL[table]["delete"], (row["record_key"],))
            continue
        seen_claims.add(claim_id)
        value = row[value_column]
        key = row["idempotency_key"] or make_idempotency_key(
            table[:-1], claim_id, value
        )
        db.execute(RECORD_SQL[table]["update"], (key, row["record_key"]))


def backfill_claim_records(db: CoreConnection) -> None:
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


def ensure_workflow_columns(db: CoreConnection) -> None:
    """Add run, lease and retry fields to pilot databases."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(claims)").fetchall()}
    for name, declaration in (
        ("failure_count", "INTEGER NOT NULL DEFAULT 0"),
        ("run_id", "TEXT"),
        ("thread_id", "TEXT"),
        ("lease_at", "TEXT"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE claims ADD COLUMN {name} {declaration}")
    run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
    if "run_order" not in run_columns:
        db.execute("ALTER TABLE runs ADD COLUMN run_order INTEGER")
        rows = db.execute(
            "SELECT run_id FROM runs ORDER BY created_at, run_id"
        ).fetchall()
        for run_order, row in enumerate(rows, start=1):
            db.execute(
                "UPDATE runs SET run_order = ? WHERE run_id = ?",
                (run_order, row["run_id"]),
            )
        run_columns.add("run_order")
    for name, declaration in (
        ("selection_complete", "INTEGER NOT NULL DEFAULT 0"),
        ("required_claims", "INTEGER"),
        ("heartbeat_at", "TEXT"),
    ):
        if name not in run_columns:
            db.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
    todo_columns = {row[1] for row in db.execute("PRAGMA table_info(todos)").fetchall()}
    if "attempt_count" not in todo_columns:
        db.execute(
            "ALTER TABLE todos ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
        )


def initialize_database() -> None:
    """Create the schema and seed the claims queue once."""
    claims = read_json("claims.json")
    policies = {row["policy_id"]: row for row in read_json("policies.json")}
    customers = {row["customer_id"]: row for row in read_json("customers.json")}
    handlers = read_json("handlers.json")
    settings = {
        "plans": read_json("plans.json"),
        "company": read_json("company.json"),
    }
    documents = {
        "policy_wording": (DATA_DIR / "policy_wording.md").read_text(encoding="utf-8"),
        "claims_manual": (DATA_DIR / "claims_manual.md").read_text(encoding="utf-8"),
    }

    if os.getenv("DATABASE_URL"):
        with engine().begin() as raw_db:
            raw_db.execute(text("CREATE SCHEMA IF NOT EXISTS undercover"))
    metadata.create_all(engine())

    with connection() as db:
        ensure_workflow_columns(db)
        seed_reference_data(
            db,
            policies=list(policies.values()),
            customers=list(customers.values()),
            handlers=handlers,
        )
        migrate_events_to_steps(db)
        seed_claims(db, claims, policies, customers)
        merge_settings(db, settings)
        save_documents(db, documents)
        db.execute(
            "DELETE FROM settings WHERE name IN ('policy_wording', 'claims_manual')"
        )

        migrate_unique_records(db, "decisions")
        migrate_unique_records(db, "payments")
        migrate_unique_records(db, "signoffs")
        ensure_unique_indexes(db)
        backfill_claim_records(db)


def seed_reference_data(
    db: CoreConnection,
    policies: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    handlers: list[dict[str, Any]],
) -> None:
    """Upsert normalized policy, customer and handler records with Core tables."""
    for table, key_name, json_name, records in (
        (policies_table, "policy_id", "policy_json", policies),
        (customers_table, "customer_id", "customer_json", customers),
        (handlers_table, "handler_id", "handler_json", handlers),
    ):
        for record in records:
            key = record[key_name]
            condition = table.c[key_name] == key
            values = {key_name: key, json_name: record}
            existing = db.execute(table.select().where(condition)).fetchone()
            if existing is None:
                db.execute(table.insert().values(**values))
            else:
                db.execute(table.update().where(condition).values(**values))


def migrate_events_to_steps(db: CoreConnection) -> None:
    """Move progress rows from the pilot events table into durable steps."""
    if "events" not in inspect(db._connection).get_table_names():
        return
    rows = db.execute(
        "SELECT event_id, claim_id, step, detail, created_at "
        "FROM events ORDER BY event_id"
    ).fetchall()
    for row in rows:
        db.execute(
            "INSERT OR IGNORE INTO steps "
            "(claim_id, step, detail, created_at) VALUES (?, ?, ?, ?)",
            (row["claim_id"], row["step"], row["detail"], row["created_at"]),
        )
    db.execute("DROP TABLE events")


def seed_claims(
    db: CoreConnection,
    claims: list[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    customers: dict[str, dict[str, Any]],
) -> None:
    """Seed claim records when the configured database is empty."""
    count_row = db.execute("SELECT COUNT(*) FROM claims").fetchone()
    if count_row is None:
        raise RuntimeError("Could not count seeded claims")
    if count_row[0]:
        return
    for claim in claims:
        policy = policies[claim["policy_id"]]
        customer = customers[claim["customer_id"]]
        db.execute(
            claims_table.insert().values(
                claim_id=claim["claim_id"],
                policy_id=claim["policy_id"],
                customer_id=claim["customer_id"],
                source_json=json.dumps(claim),
                policy_json=json.dumps(policy),
                customer_json=json.dumps(customer),
            )
        )


def merge_settings(db: CoreConnection, settings: dict[str, Any]) -> None:
    """Insert defaults and merge new keys into existing settings."""
    for name, defaults in settings.items():
        row = db.execute(
            select(settings_table.c.value).where(settings_table.c.name == name)
        ).fetchone()
        if row is None:
            db.execute(settings_table.insert().values(name=name, value=defaults))
            continue
        existing = decode_json(row["value"])
        if not isinstance(defaults, dict) or not isinstance(existing, dict):
            continue
        merged = {**defaults, **existing}
        if merged != existing:
            db.execute(
                settings_table.update()
                .where(settings_table.c.name == name)
                .values(value=merged)
            )


def save_documents(db: CoreConnection, documents: dict[str, str]) -> None:
    """Upsert ordered source documents used by agent knowledge tools."""
    for position, (name, content) in enumerate(documents.items()):
        current = db.execute(
            select(documents_table.c.name).where(documents_table.c.name == name)
        ).fetchone()
        if current is None:
            db.execute(
                documents_table.insert().values(
                    name=name, position=position, content=content
                )
            )
        else:
            db.execute(
                documents_table.update()
                .where(documents_table.c.name == name)
                .values(position=position, content=content)
            )


def ensure_unique_indexes(db: CoreConnection) -> None:
    """Create uniqueness guarantees for decisions, payments and runs."""
    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS decisions_claim_unique "
        "ON decisions(claim_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS decisions_key_unique "
        "ON decisions(idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS payments_claim_unique ON payments(claim_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS payments_key_unique "
        "ON payments(idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS signoffs_claim_unique ON signoffs(claim_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS signoffs_key_unique "
        "ON signoffs(idempotency_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS one_active_run "
        "ON runs(status) WHERE status = 'in progress'",
    ):
        db.execute(statement)


def reseed_database() -> None:
    """Rebuild only UnderCover's application tables from approved seed files."""
    with connection() as db:
        for table in (
            "events",
            "steps",
            "source_reads",
            "todos",
            "payments",
            "decisions",
            "signoffs",
            "runs",
            "documents",
            "settings",
            "claims",
            "policies",
            "customers",
            "handlers",
        ):
            db.execute(f"DROP TABLE IF EXISTS {table}")
    initialize_database()


def list_claim_records() -> list[dict[str, Any]]:
    """Return source claim records with their current workflow state."""
    with connection() as db:
        rows = db.execute(claim_view.order_by(claims_table.c.claim_id)).fetchall()
    return [claim_record(row) for row in rows]


def get_claim_record(claim_id: str) -> dict[str, Any] | None:
    """Return one source claim record and its current workflow state."""
    with connection() as db:
        row = db.execute(
            claim_view.where(claims_table.c.claim_id == claim_id)
        ).fetchone()
    return claim_record(row) if row is not None else None


def claim_record(row: RowView) -> dict[str, Any]:
    """Convert a SQLite claim row into a Python record."""
    return {
        "claim": json.loads(row["source_json"]),
        "policy": decode_json(row["normalized_policy_json"]),
        "customer": decode_json(row["normalized_customer_json"]),
        "status": row["status"],
        "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
        "sign_off_handler_id": row["sign_off_handler_id"],
        "sign_off_at": row["sign_off_at"],
        "payment": json.loads(row["payment_json"]) if row["payment_json"] else None,
        "run_id": row["run_id"],
        "thread_id": row["thread_id"],
        "failure_count": row["failure_count"],
    }


def get_setting(name: str) -> Any:
    """Return one seeded setting or approved source document."""
    if name == "handlers":
        with connection() as db:
            rows = db.execute(
                select(handlers_table.c.handler_json).order_by(
                    handlers_table.c.handler_id
                )
            ).fetchall()
        return [decode_json(row["handler_json"]) for row in rows]
    document = None
    with connection() as db:
        row = db.execute(
            select(settings_table.c.value).where(settings_table.c.name == name)
        ).fetchone()
        if row is None:
            document = db.execute(
                select(documents_table.c.content).where(documents_table.c.name == name)
            ).fetchone()
    if row is not None:
        return decode_json(row["value"])
    if document is not None:
        return document["content"]
    raise KeyError(name)


def list_events(claim_id: str) -> list[dict[str, Any]]:
    """Return ordered progress events for a claim."""
    with connection() as db:
        rows = db.execute(
            select(steps_table.c.step, steps_table.c.detail, steps_table.c.created_at)
            .where(steps_table.c.claim_id == claim_id)
            .order_by(steps_table.c.step_id)
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
            "UPDATE claims SET status = ?, thread_id = CASE "
            "WHEN ? = 'waiting' THEN NULL ELSE thread_id END, lease_at = CASE "
            "WHEN ? = 'in progress' THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE claim_id = ?",
            (status, status, status, claim_id),
        )
    if result.rowcount == 0:
        raise KeyError(claim_id)


def set_claim_thread_id(claim_id: str, thread_id: str) -> str:
    """Save the first thread id for an in-progress claim attempt."""
    with connection() as db:
        row = db.execute(
            "SELECT status, thread_id FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            raise KeyError(claim_id)
        if row["status"] != "in progress":
            raise ValueError("Claim is not in progress")
        if row["thread_id"] is not None:
            return str(row["thread_id"])
        db.execute(
            "UPDATE claims SET thread_id = ? WHERE claim_id = ? AND thread_id IS NULL",
            (thread_id, claim_id),
        )
        saved = db.execute(
            "SELECT thread_id FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if saved is None:
            raise KeyError(claim_id)
    return str(saved["thread_id"])


def add_event(claim_id: str, step: str, detail: str) -> None:
    """Persist one claim progress event."""
    with connection() as db:
        db.execute(
            "INSERT INTO steps (claim_id, step, detail) VALUES (?, ?, ?)",
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


def sign_off_claim(claim_id: str, handler_id: str) -> str:
    """Record one idempotent handler sign-off."""
    key = make_idempotency_key("signoff", claim_id, handler_id)
    with connection() as db:
        existing = db.execute(
            "SELECT idempotency_key FROM signoffs WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing is not None:
            if existing["idempotency_key"] == key:
                return signoff_created_at(db, claim_id)
            raise ValueError("Claim already has a different handler sign-off")
        result = db.execute(
            "UPDATE claims SET sign_off_handler_id = ?, status = 'in progress' "
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
        return signoff_created_at(db, claim_id)


def signoff_created_at(db: CoreConnection, claim_id: str) -> str:
    """Return the saved sign-off timestamp for one claim."""
    row = db.execute(
        "SELECT created_at FROM signoffs WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise KeyError(claim_id)
    return str(row["created_at"])


def save_payment(
    claim_id: str,
    amount_inr: float,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> None:
    """Record one idempotent simulated INR payment."""
    payment = {
        "amount_inr": amount_inr,
        "simulated": True,
        "signed_off_by": approved_by,
        "signed_off_at": approved_at,
    }
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
        except IntegrityError as error:
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
    db: CoreConnection, run_id: str, claim_ids: list[str]
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
        waiting_count_row = db.execute(
            "SELECT COUNT(*) FROM claims WHERE status = 'waiting'"
        ).fetchone()
        if waiting_count_row is None:
            raise RuntimeError("Could not count waiting claims")
        waiting_count = waiting_count_row[0]
        expected = min(run["max_claims"], waiting_count)
        if len(claim_ids) != expected:
            raise ValueError(f"Run must select exactly {expected} waiting claims")


def validate_claims_waiting(db: CoreConnection, claim_ids: list[str]) -> None:
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
                "thread_id = NULL, "
                "lease_at = CURRENT_TIMESTAMP "
                "WHERE claim_id = ? AND status = 'waiting'",
                (run_id, claim_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Claim {claim_id} was acquired by another run")
            db.execute(
                "INSERT INTO todos "
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
            "SELECT todo_id, position, claim_id, status, attempt_count FROM todos "
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
            "UPDATE todos SET position = position + ? WHERE run_id = ?",
            (offset, run_id),
        )
        for position, item in enumerate(items):
            db.execute(
                "UPDATE todos SET position = ?, description = ? "
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
            "FROM todos "
            "WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
    return {**dict(row), "todos": [dict(todo) for todo in todos]}


def get_current_run() -> dict[str, Any] | None:
    """Return the active run and its work items, if one exists."""
    with connection() as db:
        row = db.execute(
            "SELECT run_id FROM runs WHERE status = 'in progress' "
            "ORDER BY run_order DESC LIMIT 1"
        ).fetchone()
    return get_run(row["run_id"]) if row is not None else None


def get_latest_run() -> dict[str, Any] | None:
    """Return the most recently created run and its work items."""
    with connection() as db:
        row = db.execute(
            "SELECT run_id FROM runs ORDER BY run_order DESC LIMIT 1"
        ).fetchone()
    return get_run(row["run_id"]) if row is not None else None


def set_todo_status(todo_id: int, status: str) -> None:
    """Update the state of one persisted work item."""
    with connection() as db:
        todo = db.execute(
            "SELECT run_id, claim_id, status FROM todos WHERE todo_id = ?",
            (todo_id,),
        ).fetchone()
        if todo is None:
            raise KeyError(todo_id)
        db.execute(
            "UPDATE todos SET status = ?, attempt_count = attempt_count + ? "
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
            "UPDATE todos SET status = ? WHERE run_id = ? AND claim_id = ?",
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
            "SELECT todo_id FROM todos WHERE run_id = ? AND claim_id = ?",
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
                "UPDATE todos SET status = 'done' WHERE todo_id = ?",
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
            "UPDATE claims SET failure_count = ?, status = ?, thread_id = CASE "
            "WHEN ? IN ('waiting', 'needs handler') THEN NULL ELSE thread_id END, "
            "lease_at = "
            "CASE WHEN ? = 'in progress' THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE claim_id = ?",
            (failures, status, status, status, claim_id),
        )
        db.execute(
            "UPDATE todos SET status = ? WHERE todo_id = ?",
            (todo_status, todo["todo_id"]),
        )
        db.execute(
            "INSERT INTO steps (claim_id, step, detail) VALUES (?, ?, ?)",
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
            "SELECT todo_id, status, attempt_count FROM todos "
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
                "UPDATE claims SET status = 'in progress', thread_id = NULL, "
                "lease_at = "
                "CURRENT_TIMESTAMP WHERE claim_id = ? AND status = 'waiting' "
                "AND run_id = ?",
                (claim_id, run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Claim could not be reacquired for retry")
        elif claim["status"] != "in progress" or claim["decision_json"] is None:
            raise ValueError("Claim state is not retryable")
        db.execute(
            "UPDATE todos SET status = 'in progress', "
            "attempt_count = attempt_count + 1 WHERE todo_id = ?",
            (todo["todo_id"],),
        )
        db.execute(
            "INSERT INTO steps (claim_id, step, detail) VALUES (?, ?, ?)",
            (claim_id, "Attempt started", f"Attempt {int(todo['attempt_count']) + 1}."),
        )
        touch_run(db, run_id)
        return int(todo["attempt_count"]) + 1


def expired_run_ids(lease_seconds: int) -> list[str]:
    """List active runs whose heartbeat lease has expired."""
    with connection() as db:
        rows = db.execute(
            "SELECT run_id, heartbeat_at, created_at FROM runs "
            "WHERE status = 'in progress' ORDER BY created_at"
        ).fetchall()
    return [
        row["run_id"]
        for row in rows
        if lease_has_expired(row["heartbeat_at"], row["created_at"], lease_seconds)
    ]


def recover_expired_todo(db: CoreConnection, run_id: str, todo: RowView) -> bool:
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
            "UPDATE todos SET status = 'done' WHERE todo_id = ?",
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
                "UPDATE todos SET status = 'left for handler' WHERE todo_id = ?",
                (todo["todo_id"],),
            )
        return False

    failures = int(todo["failure_count"])
    if stuck:
        db.execute(
            "INSERT INTO steps (claim_id, step, detail) "
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
            "UPDATE todos SET status = 'left for handler' WHERE todo_id = ?",
            (todo["todo_id"],),
        )
        return False

    db.execute(
        "UPDATE claims SET failure_count = ?, status = 'waiting', lease_at = NULL "
        "WHERE claim_id = ?",
        (failures, todo["claim_id"]),
    )
    db.execute(
        "UPDATE todos SET status = 'to do' WHERE todo_id = ?",
        (todo["todo_id"],),
    )
    return True


def recover_expired_run(
    run_id: str, lease_seconds: int, force: bool = False
) -> str | None:
    """Interrupt an expired run and create a continuation for recoverable claims."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute(
            "SELECT * FROM runs WHERE run_id = ? AND status = 'in progress'",
            (run_id,),
        ).fetchone()
        if run is None or (
            not force
            and not lease_has_expired(
                run["heartbeat_at"], run["created_at"], lease_seconds
            )
        ):
            return None
        todos = db.execute(
            "SELECT t.todo_id, t.position, t.claim_id, t.description, t.status, "
            "t.attempt_count, "
            "c.status AS claim_status, c.failure_count, c.decision_json, "
            "c.payment_json, c.sign_off_handler_id, c.lease_at "
            "FROM todos AS t JOIN claims AS c USING (claim_id) "
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
                    "INSERT INTO todos "
                    "(run_id, position, claim_id, description, status, attempt_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        continuation_id,
                        position,
                        todo["claim_id"],
                        todo["description"],
                        todo["status"],
                        todo["attempt_count"],
                    ),
                )
        return continuation_id
