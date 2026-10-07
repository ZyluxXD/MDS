from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def parse_bool(
        value: str | None,
        *,
        default: bool = False,
        name: str = "boolean",
) -> bool:
    """Parse an environment boolean"""
    # return the default if the variable is None
    if value is None:
        return default
    # normalize the value
    normalized = value.strip().lower()
    # check for true boolean values
    if normalized in {"true", "1", "yes", "on"}:
        return True
    # check for false boolean values
    if normalized in {"false", "0", "no", "off"}:
        return False
    # raise a RuntimeError if the value is not recognized
    raise RuntimeError(
        f"Invalid boolean value for {name}; expected any one of: "
        "true/false, 1/0, yes/no, or on/off."
    )


def require_env(name: str) -> str:
    """Return a required environment variable or raise a RuntimeError if not set"""
    # get the environment variable value
    value = os.getenv(name)
    # check if the value is None or empty
    if not value or not value.strip():
        raise RuntimeError(
            f"Missing {name}. Make sure to add this to your environment variables "
            "or .env file as it is required!"
        )
    # return the value if it has a value
    return value.strip()


def parse_int(
        value: str | None,
        *,
        default: int,
        minimum: int,
        name: str,
) -> int:
    """Parse an optional integer and enforce an explicit inclusive minimum"""
    # return the default if the variable is None or empty
    if value is None or not value.strip():
        return default
    # parse the value as an integer
    try:
        parsed = int(value.strip())
    # raise a ValueError if the value is not a valid integer
    except ValueError as error:
        raise RuntimeError(
            f"Invalid integer value for {name}; expected an integer of at least {minimum}."
        ) from error
    # raise a RuntimeError if the parsed value is less than the minimum
    if parsed < minimum:
        raise RuntimeError(
            f"Invalid integer value for {name}; expected an integer of at least {minimum}."
        )
    # return the parsed value if it is valid
    return parsed


def parse_nonnegative_int(
        value: str | None,
        *,
        default: int,
        name: str,
) -> int:
    """Backward-compatible wrapper for a non-negative integer setting."""
    # run parse_int() with a minimum of 0, to check if it is non-negative
    return parse_int(value, default=default, minimum=0, name=name)


def parse_positive_int(
        value: str | None,
        *,
        default: int,
        name: str,
) -> int:
    """Backward-compatible wrapper for a positive integer setting."""
    # run parse_int() with a minimum of 1, to check if it is positive
    return parse_int(value, default=default, minimum=1, name=name)


def parse_positive_float(
        value: str | None,
        *,
        default: float,
        name: str,
) -> float:
    """Parse an optional finite, positive floating-point setting"""
    # return the default if the variable is None or empty
    if value is None or not value.strip():
        return default
    # parse the value as a float
    try:
        parsed = float(value.strip())
    # raise a ValueError if the value is not a valid float
    except ValueError as error:
        raise RuntimeError(
            f"Invalid number value for {name}; expected a positive number."
        ) from error
    # raise a RuntimeError if the parsed value is not positive or not finite
    if parsed <= 0 or not math.isfinite(parsed):
        raise RuntimeError(
            f"Invalid number value for {name}; expected a positive number."
        )
    # return the parsed value if it is valid
    return parsed


def parse_clock_time(
        value: str | None,
        *,
        default: str,
        name: str,
) -> tuple[int, int]:
    """Parse a 24-hour UTC clock time in ``HH:MM`` format."""
    # extract the raw value and split it into parts
    raw_value = (value or default).strip()
    parts = raw_value.split(":")
    # raise a RuntimeError if there are not exactly two parts
    if len(parts) != 2:
        raise RuntimeError(f"Invalid time value for {name}; expected HH:MM in UTC.")
    # parse the hour and minute parts as integers
    try:
        hour, minute = (int(part) for part in parts)
    # raise a RuntimeError if the parts are not valid integers or are out of range
    except ValueError as error:
        raise RuntimeError(f"Invalid time value for {name}; expected HH:MM in UTC.") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise RuntimeError(f"Invalid time value for {name}; expected HH:MM in UTC.")
    # return the hour and minute as a tuple if valid
    return hour, minute


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated application settings loaded explicitly at startup."""

    slack_bot_token: str = field(repr=False)
    slack_app_token: str = field(repr=False)
    slack_channel_id: str

    llm_enabled: bool = False
    llm_memes_command_moderation_enabled: bool = True
    llm_daily_post_moderation_enabled: bool = True
    llm_model: str | None = None
    llm_provider: str | None = None
    llm_endpoint: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    llm_api_key_env: str | None = None
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 60.0
    llm_moderation_strictness: str = "high"

    daily_post_enabled: bool = True
    daily_post_time: tuple[int, int] = (0, 0)
    meme_command_channel_cooldown_seconds: int = 300
    meme_command_user_cooldown_seconds: int = 60
    meme_command_channel_cooldown_exempt_ids: frozenset[str] = frozenset()
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> Settings:
        """Load dotenv and read/validate all settings from environment variables"""
        # load environment variables
        load_dotenv()
        # read the LLM API key & LLM API key environment variable name, if set
        llm_api_key_env = _optional_env("LLM_API_KEY_ENV")
        llm_api_key = _optional_env("LLM_API_KEY")
        # if the LLM API key is not set but the LLM API key environment variable name is set,
        # read the LLM API key from that environment variable
        if llm_api_key is None and llm_api_key_env is not None:
            llm_api_key = _optional_env(llm_api_key_env)
        # read the remainder of the settings from environment variables and return a Settings instance
        return cls(
            slack_bot_token=require_env("SLACK_BOT_TOKEN"),
            slack_app_token=require_env("SLACK_APP_TOKEN"),
            slack_channel_id=require_env("SLACK_CHANNEL_ID"),
            llm_enabled=parse_bool(
                os.getenv("LLM_ENABLED"), name="LLM_ENABLED", default=False
            ),
            llm_memes_command_moderation_enabled=parse_bool(
                os.getenv("LLM_MEMES_COMMAND_MODERATION_ENABLED"),
                name="LLM_MEMES_COMMAND_MODERATION_ENABLED",
                default=True,
            ),
            llm_daily_post_moderation_enabled=parse_bool(
                os.getenv("LLM_DAILY_POST_MODERATION_ENABLED"),
                name="LLM_DAILY_POST_MODERATION_ENABLED",
                default=True,
            ),
            llm_model=_optional_env("LLM_MODEL"),
            llm_provider=_optional_env("LLM_PROVIDER"),
            llm_endpoint=_optional_env("LLM_ENDPOINT"),
            llm_api_key=llm_api_key,
            llm_api_key_env=llm_api_key_env,
            llm_max_tokens=parse_int(
                os.getenv("LLM_MAX_TOKENS"),
                default=4096,
                minimum=1,
                name="LLM_MAX_TOKENS",
            ),
            llm_timeout_seconds=parse_positive_float(
                os.getenv("LLM_TIMEOUT_SECONDS"),
                default=60.0,
                name="LLM_TIMEOUT_SECONDS",
            ),
            llm_moderation_strictness=(
                    os.getenv("LLM_MODERATION_STRICTNESS", "high").strip().lower() or "high"
            ),
            daily_post_enabled=parse_bool(
                os.getenv("DAILY_POST_ENABLED"),
                name="DAILY_POST_ENABLED",
                default=True,
            ),
            daily_post_time=parse_clock_time(
                os.getenv("DAILY_POST_TIME"), default="00:00", name="DAILY_POST_TIME"
            ),
            meme_command_channel_cooldown_seconds=parse_int(
                os.getenv("MEME_COMMAND_CHANNEL_COOLDOWN_SECONDS"),
                default=300,
                minimum=0,
                name="MEME_COMMAND_CHANNEL_COOLDOWN_SECONDS",
            ),
            meme_command_user_cooldown_seconds=parse_int(
                os.getenv("MEME_COMMAND_USER_COOLDOWN_SECONDS"),
                default=60,
                minimum=0,
                name="MEME_COMMAND_USER_COOLDOWN_SECONDS",
            ),
            meme_command_channel_cooldown_exempt_ids=frozenset(
                channel_id.strip()
                for channel_id in os.getenv("MEME_COMMAND_CHANNEL_COOLDOWN_EXEMPT_IDS", "").split(",")
                if channel_id.strip()
            ),
            log_level=(os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper(),
        )


def _optional_env(name: str) -> str | None:
    """Return an optional environment variable or None if not set or empty"""
    # get the environment variable value
    value = os.getenv(name)
    # return None if the value is None or empty, otherwise return the stripped value
    return value.strip() if value and value.strip() else None
