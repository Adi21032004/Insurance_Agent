from __future__ import annotations

from typing import Any

from fastapi import Depends

from backend import database


def get_db() -> Any:
    """Return a shared database connection context for request-scoped operations."""
    return database.connection()


DatabaseDep = Depends(get_db)
