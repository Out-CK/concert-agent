"""
CLI entrypoint for the NYC Event Agent.

Usage:
    python main.py --run-now        # Trigger a Concert Run immediately
    python main.py --schedule       # Start the daily scheduler (blocks until Ctrl+C)
    python main.py --art-run        # Trigger an Art Gallery Run immediately
    python main.py --tiktok         # Trigger a TikTok Concert Run immediately
    python main.py --ticketing-run  # Query Ticketmaster/SeatGeek/Eventbrite/StubHub directly
    python main.py --enrich-venues  # Find addresses for unmapped venues
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
    group.add_argument("--instagram", action="store_true", help="Trigger an Instagram Run immediately")
    group.add_argument("--instagram-schedule", action="store_true", help="Start the daily scheduler (concert + Instagram)")
    group.add_argument("--art-run", action="store_true", help="Trigger an Art Gallery Run immediately")
    group.add_argument("--tiktok", action="store_true", help="Trigger a TikTok Concert Run immediately")
    group.add_argument("--ticketing-run", action="store_true", help="Query Ticketmaster/SeatGeek/Eventbrite/StubHub directly")
    group.add_argument("--enrich-venues", action="store_true", help="Find addresses for unmapped venues")
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

    elif args.instagram:
        logger.info("Mode: --instagram | Triggering immediate Instagram Run")
        from agent.instagram_agent import InstagramAgent
        InstagramAgent().run()

    elif args.instagram_schedule:
        logger.info("Mode: --instagram-schedule | Starting daily scheduler (concert + Instagram)")
        from scheduler.job_scheduler import start_scheduler
        start_scheduler(include_instagram=True)

    elif args.art_run:
        logger.info("Mode: --art-run | Triggering immediate Art Gallery Run")
        from agent.art_agent import ArtAgent
        ArtAgent().run()

    elif args.tiktok:
        logger.info("Mode: --tiktok | Triggering immediate TikTok Concert Run")
        from agent.tiktok_agent import TikTokConcertAgent
        TikTokConcertAgent().run()

    elif args.ticketing_run:
        logger.info("Mode: --ticketing-run | Querying ticketing platforms directly")
        from ticketing.ticketing_agent import TicketingAgent
        TicketingAgent().run()

    elif args.enrich_venues:
        logger.info("Mode: --enrich-venues | Finding addresses for unmapped venues")
        from agent.venue_enricher import VenueEnricher
        VenueEnricher(event_type="concert").run()


if __name__ == "__main__":
    main()
