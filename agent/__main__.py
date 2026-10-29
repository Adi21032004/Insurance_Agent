"""Command-line entry point for UnderCover claim runs."""

import argparse

from dotenv import load_dotenv

from agent.harness import checkpointer, model, run_claim_direct, run_queue


def main() -> None:
    """Run the capped queue or one named claim."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the UnderCover Agent")
    parser.add_argument("claim_id", nargs="?", help="Work one named claim")
    parser.add_argument(
        "--cap", type=int, default=25, help="Queue run cap from 1 to 25"
    )
    arguments = parser.parse_args()
    with checkpointer() as saver:
        if arguments.claim_id:
            print(run_claim_direct(arguments.claim_id, saver))
        else:
            print(run_queue(arguments.cap, model(), saver=saver))


if __name__ == "__main__":
    main()
