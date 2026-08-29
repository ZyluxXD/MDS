import os
from dotenv import load_dotenv

load_dotenv()


def require_env(name: str) -> str:
    """ensure env vars are set"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing {name}. Make sure to add this to your environment variables or .env file!"
        )
    return value


SLACK_BOT_TOKEN = require_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = require_env("SLACK_APP_TOKEN")
SLACK_CHANNEL_ID = require_env("SLACK_CHANNEL_ID")
