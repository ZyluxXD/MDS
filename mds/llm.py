from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any
from . import config as app_config
from langchain.chat_models import init_chat_model


class ConfigurationError(RuntimeError):
    """error raised for invalid or missing LLM configuration settings"""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Settings for the configured LangChain model."""

    model: str
    provider: str
    endpoint: str | None = None
    api_key: str | None = field(default=None, repr=False)
    api_key_env: str | None = None
    max_tokens: int = int(app_config.LLM_MAX_TOKENS)


def _required(value: str | None, name: str) -> str:
    """returns a setting or raises a configuration error"""
    if value is None or not value.strip():
        raise ConfigurationError(f"Missing required LLM setting: {name}.")
    return value


def _max_tokens(value: str | None) -> int:
    """parse the optional completion-token cap"""
    if value is None or not value.strip():
        return int(app_config.LLM_MAX_TOKENS)

    try:
        max_tokens = int(value)
    except ValueError as error:
        raise ConfigurationError(
            "LLM_MAX_TOKENS must be a positive integer."
        ) from error
    if max_tokens < 1:
        raise ConfigurationError("LLM_MAX_TOKENS must be a positive integer.")
    return max_tokens


def get_llm_config() -> LLMConfig | None:
    """get the llm settings"""
    if not app_config.LLM_ENABLED:
        return None

    model = _required(app_config.LLM_MODEL, "LLM_MODEL").strip()
    provider = _required(app_config.LLM_PROVIDER, "LLM_PROVIDER").strip().lower()
    if provider == "gemini":
        provider = "google_genai"
    if provider not in {"openai", "anthropic", "google_genai"}:
        raise ConfigurationError(
            "Unsupported LLM_PROVIDER, use either 'openai', 'anthropic', or 'gemini'. This is the API request format "
            "specification type (with the exception of gemini), not the actual API you need to use."
        )
    endpoint = app_config.LLM_ENDPOINT
    if endpoint is not None:
        endpoint = endpoint.strip() or None
    if endpoint is not None and provider == "google_genai":
        raise ConfigurationError(
            "LLM_ENDPOINT is not supported for the google_genai (gemini) provider, remove it to use Gemini's native endpoint"
        )

    api_key = app_config.LLM_API_KEY
    api_key_env: str | None = None
    if api_key is None or not api_key.strip():
        api_key_env = _required(app_config.LLM_API_KEY_ENV, "LLM_API_KEY_ENV").strip()
        api_key = os.environ.get(api_key_env) if api_key_env else api_key
        if api_key is None or not api_key.strip():
            raise ConfigurationError(
                "LLM_API_KEY_ENV must name a set environment variable "
                f"({api_key_env!r} is not set)."
            )
    api_key = api_key.strip()
    max_tokens = _max_tokens(app_config.LLM_MAX_TOKENS)
    return LLMConfig(
        model=model,
        provider=provider,
        endpoint=endpoint,
        api_key=api_key,
        api_key_env=api_key_env,
        max_tokens=max_tokens,
    )


def create_llm_model(llm_config: LLMConfig | None = None) -> Any | None:
    """create a LangChain model instance"""
    config = get_llm_config() if llm_config is None else llm_config
    if config is None:
        return None

    model_options: dict[str, Any] = {"model_provider": config.provider}
    model_options["max_tokens"] = config.max_tokens
    if config.api_key is not None:
        model_options["api_key"] = config.api_key
    if config.endpoint is not None:
        # the OpenAI and Anthropic adapters have the base_url field, allowing you to use custom endpoints
        # but Gemini uses its own endpoint exclusively
        if config.provider == "google_genai" or config.provider == "gemini":
            raise ConfigurationError(
                "LLM_ENDPOINT is not supported for the gemini provider option"
            )
        model_options["base_url"] = config.endpoint
    return init_chat_model(config.model, **model_options)
