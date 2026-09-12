import os
from dotenv import load_dotenv

load_dotenv()


def parse_bool(
        value: str | None,
        *,
        default: bool = False,
        name: str = "boolean",
) -> bool:
    """parse an environment boolean value"""
    if value is None:
        return default
    if not isinstance(value, str):
        raise RuntimeError(f"Invalid boolean value for {name!s}; expected a string.")

    normalized = value.strip().lower()
    if normalized in frozenset({"true", "1", "yes", "on"}):
        return True
    if normalized in frozenset({"false", "0", "no", "off"}):
        return False
    raise RuntimeError(f"Invalid boolean value for {name!s}; expected any one of: true/false, 1/0, yes/no, or on/off.")


def require_env(name: str) -> str:
    """ensure env vars are set"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing {name}. Make sure to add this to your environment variables or .env file as it is required!"
        )
    return value


# Slack configuration
SLACK_BOT_TOKEN = require_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = require_env("SLACK_APP_TOKEN")
SLACK_CHANNEL_ID = require_env("SLACK_CHANNEL_ID")

# LLM Configuration
# LLM processing is disabled by default, but you can set LLM_ENABLED=true to enable it
LLM_ENABLED = parse_bool(os.getenv("LLM_ENABLED"), name="LLM_ENABLED", default=False)
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_PROVIDER = os.getenv("LLM_PROVIDER")  # openai, anthropic, or gemini
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT")  # optional custom base URL for supported providers
LLM_API_KEY = os.getenv("LLM_API_KEY")  # preferred credential source when set
LLM_API_KEY_ENV = os.getenv("LLM_API_KEY_ENV")  # names the environment variable holding the key
LLM_MAX_TOKENS = os.getenv("LLM_MAX_TOKENS", "1000")  # optional maximum completion tokens, defaults to 1000 if not set
