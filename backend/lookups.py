from __future__ import annotations

from typing import Any


def find_run(runs: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    """Return one run by id from a list."""
    for run in runs:
        if run.get("run_id") == run_id:
            return run
    return None


def claim_not_found_message(claim_id: str) -> str:
    """Return the canonical message for missing claims."""
    return f"Claim {claim_id} not found"


def run_not_found_message(run_id: str) -> str:
    """Return the canonical message for missing runs."""
    return f"Run {run_id} not found"
