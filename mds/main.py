import logging
import os
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError
import time
from datetime import datetime, timedelta, timezone
from threading import Thread
from .slack import post_memes, app
from .config import SLACK_APP_TOKEN
from .llm import get_llm_config

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """configure logging settings and format"""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_scheduler() -> None:
    """run the daily schedular to post at 00:00 UTC daily"""
    while True:
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        seconds_until_next_run = (next_run - now).total_seconds()
        logger.info("Next daily post scheduled for %s UTC", next_run.isoformat())
        time.sleep(seconds_until_next_run)

        try:
            logger.info("Starting scheduled daily meme post")
            post_memes()
        except Exception as err:
            logger.exception("Daily meme post failed: %s", err)


def manual_post() -> None:
    """manual test post on demand using the command line"""
    logger.info("Press Enter to trigger a manual post")
    while True:
        input("")
        logger.info("Manual post requested, posting after 5 seconds (press Enter again to cancel)")
        input_thread = Thread(target=input)
        input_thread.start()
        input_thread.join(timeout=5)
        if input_thread.is_alive():
            logger.info("Starting manual meme post...")
            try:
                post_memes()
            except Exception as err:
                logger.exception("Manual meme post failed: %s", err)
        else:
            logger.info("Manual post canceled")


if __name__ == "__main__":
    print("Starting the Meme Delivery Service server...")
    # configure the logging settings
    configure_logging()
    # validate llm config
    llm_config = get_llm_config()
    if llm_config is None:
        logger.info("LLM moderation is disabled")
    else:
        logger.info(
            "LLM moderation is enabled (provider=%s, model=%s, max_tokens=%d)",
            llm_config.provider,
            llm_config.model,
            llm_config.max_tokens,
        )
    # configure the Slack Socket Mode handler
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)

    # start the scheduler and manual post threads
    try:
        Thread(target=manual_post, daemon=True).start()
        Thread(target=run_scheduler, daemon=True).start()
        logger.info("Starting Slack Socket Mode listener...")
        handler.start()
    except KeyboardInterrupt:
        # exiting on SIGINT
        logger.info("Shutdown requested, exiting...")
    except SlackApiError as error:
        # error from slack
        logger.exception("Slack API error: %s", error.response.get("error"))
        raise
    finally:
        # cleanup
        handler.close()
