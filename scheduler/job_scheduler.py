import time

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from utils.logger import get_logger

logger = get_logger(__name__)

eastern = pytz.timezone("America/New_York")


def run_concert_run() -> None:
    """Entry point called by the scheduler."""
    from agent.concert_agent import ConcertAgent
    logger.info("Scheduled Concert Run triggered")
    try:
        ConcertAgent().run()
    except Exception as e:
        logger.error(f"Scheduled Concert Run failed: {e}", exc_info=True)


def run_ticketing_run() -> None:
    """Entry point for the daily Ticketing Run (Ticketmaster/SeatGeek/Eventbrite/StubHub)."""
    from ticketing.ticketing_agent import TicketingAgent
    logger.info("Scheduled Ticketing Run triggered")
    try:
        TicketingAgent().run()
    except Exception as e:
        logger.error(f"Scheduled Ticketing Run failed: {e}", exc_info=True)


def run_art_run() -> None:
    """Entry point for the daily Art Gallery Run."""
    from agent.art_agent import ArtAgent
    logger.info("Scheduled Art Run triggered")
    try:
        ArtAgent().run()
    except Exception as e:
        logger.error(f"Scheduled Art Run failed: {e}", exc_info=True)


def run_venue_enricher() -> None:
    """Entry point for the daily Venue Enricher run."""
    from agent.venue_enricher import VenueEnricher
    logger.info("Scheduled Venue Enricher triggered")
    try:
        VenueEnricher().run()
    except Exception as e:
        logger.error(f"Scheduled Venue Enricher failed: {e}", exc_info=True)


def run_instagram_run() -> None:
    """Entry point for the daily Instagram Run."""
    from agent.instagram_agent import InstagramAgent
    logger.info("Scheduled Instagram Run triggered")
    try:
        InstagramAgent().run()
    except Exception as e:
        logger.error(f"Scheduled Instagram Run failed: {e}", exc_info=True)


def start_scheduler(include_instagram: bool = False) -> None:
    """Start the APScheduler and block until Ctrl+C."""
    scheduler = BackgroundScheduler(timezone=eastern)
    scheduler.add_job(
        run_concert_run,
        trigger=CronTrigger(hour=9, minute=0, timezone=eastern),
        id="daily_concert_run",
        name="Daily NYC Concert Run",
        replace_existing=True,
    )
    scheduler.add_job(
        run_ticketing_run,
        trigger=CronTrigger(hour=9, minute=15, timezone=eastern),
        id="daily_ticketing_run",
        name="Daily Ticketing Platform Run",
        replace_existing=True,
    )
    scheduler.add_job(
        run_art_run,
        trigger=CronTrigger(hour=9, minute=45, timezone=eastern),
        id="daily_art_run",
        name="Daily Art Gallery Run",
        replace_existing=True,
    )
    scheduler.add_job(
        run_venue_enricher,
        trigger=CronTrigger(hour=10, minute=30, timezone=eastern),
        id="daily_venue_enricher",
        name="Daily Venue Enricher",
        replace_existing=True,
    )
    if include_instagram:
        scheduler.add_job(
            run_instagram_run,
            trigger=CronTrigger(hour=10, minute=0, timezone=eastern),
            id="daily_instagram_run",
            name="Daily Instagram Concert Run",
            replace_existing=True,
        )
    scheduler.start()
    logger.info("Scheduler started — Concert Run fires daily at 09:00 America/New_York")
    logger.info("Ticketing Run fires daily at 09:15 America/New_York")
    logger.info("Art Run fires daily at 09:45 America/New_York")
    logger.info("Venue Enricher fires daily at 10:30 America/New_York")
    if include_instagram:
        logger.info("Instagram Run fires daily at 10:00 America/New_York")
    logger.info("Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler…")
        scheduler.shutdown()
        logger.info("Scheduler stopped")
