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


def start_scheduler() -> None:
    """Start the APScheduler and block until Ctrl+C."""
    scheduler = BackgroundScheduler(timezone=eastern)
    scheduler.add_job(
        run_concert_run,
        trigger=CronTrigger(hour=9, minute=0, timezone=eastern),
        id="daily_concert_run",
        name="Daily NYC Concert Run",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — Concert Run fires daily at 09:00 America/New_York")
    logger.info("Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler…")
        scheduler.shutdown()
        logger.info("Scheduler stopped")
