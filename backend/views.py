from __future__ import annotations

from typing import Any


def to_claim(record: dict[str, Any]) -> dict[str, Any]:
    """Project a claim view into a consistent API payload."""
    return {
        "claim_id": record["claim"]["claim_id"],
        "status": record["status"],
        "decision": record["decision"],
        "payment": record["payment"],
        "claim": record["claim"],
        "policy": record["policy"],
        "customer": record["customer"],
        "sign_off_handler_id": record["sign_off_handler_id"],
        "sign_off_at": record["sign_off_at"],
        "thread_id": record.get("thread_id"),
    }


def to_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Project a run row into a summary-friendly payload."""
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "summary": run.get("summary"),
        "max_claims": run.get("max_claims"),
        "run_type": run.get("run_type"),
    }
