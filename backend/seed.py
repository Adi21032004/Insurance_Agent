"""Reseed the configured UnderCover database."""

import os

from backend import database


def main() -> None:
    """Rebuild the application tables and report the seeded record counts."""
    database.reseed_database()
    claims = database.read_json("claims.json")
    policies = database.read_json("policies.json")
    customers = database.read_json("customers.json")
    handlers = database.read_json("handlers.json")
    held_out_count, decision_count = database.evaluation_seed_counts()
    if os.getenv("DATABASE_URL"):
        destination = "the undercover schema on Postgres"
    else:
        destination = "undercover.db"
    print(
        f"Seeded {len(claims)} claims, {len(policies)} policies, "
        f"{len(customers)} customers and {len(handlers)} handlers into {destination}, "
        f"with {held_out_count} held-out claims and {decision_count} handlers' "
        "decisions for evaluations"
    )


if __name__ == "__main__":
    main()
