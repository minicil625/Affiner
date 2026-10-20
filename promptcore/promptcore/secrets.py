"""
Shared API key management using OS keyring (Windows Credential Manager).
Service name is unified as 'PromptCore' so both TextPrompt and VoicePrompt
share keys entered once.
"""

from __future__ import annotations

import logging
import os
from typing import Final

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)

SERVICE_NAME: Final[str] = "PromptCore"

KNOWN_PROVIDERS: Final[tuple[str, ...]] = (
    "groq",
    "cerebras",
    "gemini",
    "openrouter",
    "github_models",
    "mistral",
)

ENV_VAR_MAP: Final[dict[str, str]] = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "github_models": "GITHUB_MODELS_TOKEN",
    "mistral": "MISTRAL_API_KEY",
}


def get(provider: str) -> str | None:
    """
    Retrieve the API key for a provider from Windows Credential Manager (service 'PromptCore'),
    falling back to standard environment variables if present.
    """
    norm_provider = provider.strip().lower()
    try:
        secret = keyring.get_password(SERVICE_NAME, norm_provider)
        if secret and secret.strip():
            return secret.strip()
    except KeyringError as exc:
        logger.warning("Keyring read failed for provider '%s': %s", norm_provider, exc)

    env_name = ENV_VAR_MAP.get(norm_provider)
    if env_name:
        env_val = os.environ.get(env_name)
        if env_val and env_val.strip():
            return env_val.strip()

    return None


def set_key(provider: str, api_key: str) -> bool:
    """Store an API key in the OS keyring under service 'PromptCore'."""
    norm_provider = provider.strip().lower()
    cleaned_key = api_key.strip()
    if not cleaned_key:
        return False
    try:
        keyring.set_password(SERVICE_NAME, norm_provider, cleaned_key)
        return True
    except KeyringError as exc:
        logger.error("Failed to store key in keyring for '%s': %s", norm_provider, exc)
        return False


def delete_key(provider: str) -> bool:
    """Remove a provider's API key from the OS keyring."""
    norm_provider = provider.strip().lower()
    try:
        keyring.delete_password(SERVICE_NAME, norm_provider)
        return True
    except KeyringError:
        return False


def list_configured_providers() -> list[str]:
    """Return a list of known providers that currently have a non-empty key configured."""
    return [p for p in KNOWN_PROVIDERS if get(p) is not None]


def redact_key(text: str) -> str:
    """Redact any configured API keys from log or error strings."""
    if not text:
        return text
    redacted = text
    for provider in KNOWN_PROVIDERS:
        secret = get(provider)
        if secret and len(secret) >= 8 and secret in redacted:
            masked = f"{secret[:4]}...{secret[-3:]}"
            redacted = redacted.replace(secret, f"[REDACTED:{provider}:{masked}]")
    return redacted