from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.chat_models import init_chat_model

from .config import Settings


class ConfigurationError(RuntimeError):
    """Raised when the LLM configuration is invalid or incomplete"""


STRICTNESS_SETTINGS = frozenset({"lenient", "balanced", "strict", "high"})


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Settings for one provider & model config"""
    model: str
    provider: str
    endpoint: str | None = None
    api_key: str | None = field(default=None, repr=False)
    api_key_env: str | None = None
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    strictness: str = "high"


def _required(value: str | None, name: str) -> str:
    """Raise a ConfigurationError if the value is missing or blank"""
    # strip the value and check if it is None or empty
    if value is None or not value.strip():
        raise ConfigurationError(f"Missing required LLM setting: {name}.")
    return value.strip()


def get_llm_config(settings: Settings) -> LLMConfig | None:
    """Validate settings and build an LLM config object"""
    # check if LLM moderation is enabled, and return None if not
    if not settings.llm_enabled:
        return None
    # validate required settings and normalize values
    model = _required(settings.llm_model, "LLM_MODEL")
    provider = _required(settings.llm_provider, "LLM_PROVIDER").lower()
    provider = {"gemini": "google_genai", "google": "google_genai"}.get(provider, provider)
    if provider not in {"openai", "anthropic", "google_genai"}:
        raise ConfigurationError(
            "Unsupported LLM_PROVIDER, use either 'openai', 'anthropic', or 'gemini'. "
            "This is the API request format specification type (with the exception of gemini), "
            "not the actual API you need to use."
        )

    # validate llm endpoint and llm_api_key settings
    endpoint = settings.llm_endpoint.strip() if settings.llm_endpoint else None
    if endpoint and provider == "google_genai":
        raise ConfigurationError(
            "LLM_ENDPOINT is not supported for the google_genai (gemini) provider, "
            "remove it to use Gemini's native endpoint"
        )

    api_key = settings.llm_api_key.strip() if settings.llm_api_key else None
    api_key_env = settings.llm_api_key_env
    if not api_key:
        api_key_env = _required(api_key_env, "LLM_API_KEY_ENV")
        raise ConfigurationError(
            "LLM_API_KEY_ENV must name a set environment variable "
            f"({api_key_env!r} is not set)."
        )

    # validate strictness, max tokens, and timeout settings
    strictness = (settings.llm_moderation_strictness or "high").strip().lower()
    if strictness not in STRICTNESS_SETTINGS:
        raise ConfigurationError(
            "Invalid LLM_MODERATION_STRICTNESS; expected from the following: "
            "'lenient', 'balanced', 'high', or 'strict'."
        )
    if settings.llm_max_tokens < 1:
        raise ConfigurationError("LLM_MAX_TOKENS must be a positive integer.")
    if settings.llm_timeout_seconds <= 0:
        raise ConfigurationError("LLM_TIMEOUT_SECONDS must be a positive number.")

    # return the final object
    return LLMConfig(
        model=model,
        provider=provider,
        endpoint=endpoint,
        api_key=api_key,
        api_key_env=api_key_env,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        strictness=strictness,
    )


def create_llm_model(config: LLMConfig) -> Any:
    """Create a LangChain model from a LLMConfig object"""
    # build the model options dictionary
    model_options: dict[str, Any] = {
        "model_provider": config.provider,
        "max_tokens": config.max_tokens,
        "timeout": config.timeout_seconds,
    }
    # obtain the endpoint api key
    if config.api_key is not None:
        model_options["api_key"] = config.api_key
    if config.endpoint is not None:
        if config.provider == "google_genai":
            raise ConfigurationError(
                "LLM_ENDPOINT is not supported for the gemini provider option"
            )
        model_options["base_url"] = config.endpoint
    # return the model instance
    return init_chat_model(config.model, **model_options)
