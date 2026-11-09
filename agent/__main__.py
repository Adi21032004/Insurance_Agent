"""Command-line entry point for one UnderCover claim."""

import argparse

from dotenv import load_dotenv

from agent.harness import checkpoint_pool, run_claim_direct


def main() -> None:
    """Run one named claim."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run one UnderCover claim")
    parser.add_argument("claim_id", help="Claim identifier to work")
    arguments = parser.parse_args()
    with checkpoint_pool() as pool:
        print(run_claim_direct(arguments.claim_id, pool))


if __name__ == "__main__":
    main()
