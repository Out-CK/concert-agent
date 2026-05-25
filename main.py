"""
CLI entrypoint for the Concert Agent.

Usage:
    python main.py --run-now      # Trigger a Concert Run immediately
    python main.py --schedule     # Start the daily scheduler (blocks until Ctrl+C)
"""
import argparse
import os
import sys

from dotenv import load_dotenv

from utils.logger import get_logger, setup_root_logger

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "NIMBLE_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
]


def validate_env() -> None:
    """Fail fast if any required environment variable is missing."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        print(
            f"ERROR: Missing required environment variable(s): {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in all values.",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    load_dotenv()
    setup_root_logger()
    validate_env()

    logger = get_logger(__name__)

    parser = argparse.ArgumentParser(description="Concert Agent CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-now", action="store_true", help="Trigger a Concert Run immediately")
    group.add_argument("--schedule", action="store_true", help="Start the daily scheduler")
    args = parser.parse_args()

    # Initialize Supabase client eagerly to catch config errors before running
    from db.supabase_client import get_supabase_client
    get_supabase_client()

    if args.run_now:
        logger.info("Mode: --run-now | Triggering immediate Concert Run")
        from agent.concert_agent import ConcertAgent
        ConcertAgent().run()

    elif args.schedule:
        logger.info("Mode: --schedule | Starting daily scheduler")
        from scheduler.job_scheduler import start_scheduler
        start_scheduler()


if __name__ == "__main__":
    main()
