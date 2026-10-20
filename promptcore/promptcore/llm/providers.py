"""
Six free-tier OpenAI-compatible provider presets (§4.3, Task 3.1):
groq, cerebras, gemini, openrouter, github_models, mistral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class ProviderPreset:
    """Static capabilities and endpoint metadata for a cloud provider."""

    name: str
    base_url: str
    enabled_by_default: bool
    supports_json_schema: bool
    supports_json_object: bool
    supports_stream: bool
    supports_reasoning_effort: bool
    trains_on_free_data: bool
    extra_headers: dict[str, str] = field(default_factory=dict)


PRESETS: Final[dict[str, ProviderPreset]] = {
    "groq": ProviderPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        enabled_by_default=True,
        supports_json_schema=False,  # json_object is universally supported across Groq Llama models
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=False,
        trains_on_free_data=False,
    ),
    "cerebras": ProviderPreset(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        enabled_by_default=True,
        supports_json_schema=False,
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=False,
        trains_on_free_data=False,
    ),
    "gemini": ProviderPreset(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        enabled_by_default=True,
        supports_json_schema=True,
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=True,
        trains_on_free_data=True,  # Free tier (no billing) may be used by Google for training
    ),
    "openrouter": ProviderPreset(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        enabled_by_default=True,
        supports_json_schema=False,
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=False,
        trains_on_free_data=True,  # Model-dependent on :free tier unless blocked in account settings
        extra_headers={
            "HTTP-Referer": "https://github.com/promptcore/textprompt",
            "X-Title": "PromptCore",
        },
    ),
    "github_models": ProviderPreset(
        name="github_models",
        base_url="https://models.inference.ai.azure.com",
        enabled_by_default=False,
        supports_json_schema=False,
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=False,
        trains_on_free_data=False,
    ),
    "mistral": ProviderPreset(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        enabled_by_default=False,
        supports_json_schema=False,
        supports_json_object=True,
        supports_stream=True,
        supports_reasoning_effort=False,
        trains_on_free_data=True,  # Free "Experiment" tier trains on data unless opted out
    ),
}


def get_preset(provider: str) -> ProviderPreset:
    """Return the preset for `provider` (raises KeyError if unknown)."""
    norm = provider.strip().lower()
    if norm not in PRESETS:
        raise KeyError(f"Unknown provider preset: '{provider}'")
    return PRESETS[norm]