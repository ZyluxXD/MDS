from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from queue import Empty, Queue
from threading import Thread
import time

from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from .config import Settings
from .llm import get_llm_config
from .slack import SlackPost, create_slack_app

logger = logging.getLogger(__name__)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure the logging format and level"""
    # configure the logging config with a nice to read format and the specified log level
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_scheduler(poster: SlackPost, settings: Settings) -> None:
    """Run the daily scheduler at the configured time"""
    # check if daily posting is enabled, and return early if not
    if not settings.daily_post_enabled:
        logger.info("Daily scheduled posts are disabled")
        return
    # get the scheduled hour and minute from settings, and run the scheduler loop
    schedule_hour, schedule_minute = settings.daily_post_time
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(
            hour=schedule_hour,
            minute=schedule_minute,
            second=0,
            microsecond=0,
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        seconds_until_next_run = (next_run - now).total_seconds()
        logger.info("Next daily post scheduled for %s UTC", next_run.isoformat())
        time.sleep(seconds_until_next_run)
        try:
            logger.info("Starting scheduled daily meme post")
            poster.post_scheduled()
        except Exception as error:
            logger.exception("Daily meme post failed: %s", error)


def manual_post(poster: SlackPost) -> None:
    """Start the Enter/cancel manual posting loop poller"""
    logger.info("Press Enter to trigger a manual post")
    console_input: Queue[str | None] = Queue()

    # read from console input for manual post requests
    def read_console() -> None:
        try:
            while True:
                console_input.put(input(""))
        except EOFError:
            console_input.put(None)

    # start the console input reader thread and handle manual post requests
    Thread(target=read_console, daemon=True).start()
    while True:
        if console_input.get() is None:
            return
        logger.warning("Manual post requested, posting after 5 seconds (press Enter again to cancel)")
        try:
            canceled = console_input.get(timeout=5)
        except Empty:
            logger.warning("Starting manual meme post...")
            try:
                poster.post_manual()
            except Exception as error:
                logger.exception("Manual meme post failed: %s", error)
        else:
            logger.info("Manual post canceled")
            if canceled is None:
                return


def main() -> None:
    """Load settings, configure the app, and start the Socket Mode server"""
    print("Starting the Meme Delivery Service server...")
    # obtain the settings
    settings = Settings.from_environment()
    # configure logging
    configure_logging(settings.log_level)
    # configure LLM moderation
    llm_config = get_llm_config(settings)
    if llm_config is None:
        logger.info("LLM moderation is disabled")
    else:
        logger.info(
            "LLM moderation is enabled (provider=%s, model=%s, max_tokens=%d)",
            llm_config.provider,
            llm_config.model,
            llm_config.max_tokens,
        )

    # create the Slack app and poster
    slack_app, poster = create_slack_app(settings, llm_config)
    # start the Socket Mode handler
    handler = SocketModeHandler(slack_app, settings.slack_app_token)
    try:
        # start the manual post thread
        Thread(target=manual_post, args=(poster,), daemon=True).start()
        # start the daily scheduler thread if enabled
        if settings.daily_post_enabled:
            Thread(target=run_scheduler, args=(poster, settings), daemon=True).start()
        else:
            logger.info("Daily scheduled posts are disabled")
        logger.info("Starting Slack Socket Mode listener...")
        handler.start()
    except KeyboardInterrupt:
        # stop on CTRL+C
        logger.info("Shutdown requested, exiting...")
    except SlackApiError as error:
        # raise an exception on a Slack API error
        logger.exception("Slack API error: %s", error.response.get("error"))
        raise
    finally:
        # close the handler
        handler.close()


if __name__ == "__main__":
    main()
