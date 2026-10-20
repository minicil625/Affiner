"""
Typed configuration schema for promptcore (Section 4.14).
Shared between TextPrompt AI and VoicePrompt AI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class ModelAliases(BaseModel):
    fast: str
    quality: str


class ProviderLimits(BaseModel):
    rpm: int = 30
    rpd: int = 1000
    tpm: int = 30000
    tpd: int = 500000


class ProviderConfig(BaseModel):
    enabled: bool = True
    models: ModelAliases
    limits: ProviderLimits = Field(default_factory=ProviderLimits)


class HedgeConfig(BaseModel):
    enabled: bool = False
    after_ms: int = 600
    trigger: Literal["first_token", "full_response"] = "first_token"


class ModeRouteConfig(BaseModel):
    chain: list[str]
    stream: bool = False
    hedge: HedgeConfig = Field(default_factory=HedgeConfig)
    timeout_ms: int = 1500


class RoutingConfig(BaseModel):
    clean_only: ModeRouteConfig = Field(
        default_factory=lambda: ModeRouteConfig(
            chain=["groq.fast", "cerebras.fast", "gemini.fast", "openrouter.fast"],
            stream=True,
            hedge=HedgeConfig(enabled=True, after_ms=600, trigger="first_token"),
            timeout_ms=1500,
        )
    )
    clean_plus_improved: ModeRouteConfig = Field(
        default_factory=lambda: ModeRouteConfig(
            chain=["groq.quality", "gemini.quality", "cerebras.quality", "openrouter.quality"],
            stream=False,
            hedge=HedgeConfig(enabled=False, after_ms=1600, trigger="full_response"),
            timeout_ms=5000,
        )
    )
    structured_json: ModeRouteConfig = Field(
        default_factory=lambda: ModeRouteConfig(
            chain=["gemini.quality", "groq.quality", "cerebras.quality", "openrouter.quality"],
            stream=False,
            hedge=HedgeConfig(enabled=False, after_ms=1600, trigger="full_response"),
            timeout_ms=4000,
        )
    )
    latency_ranking: bool = True
    quota_safety_margin: float = Field(default=0.10, ge=0.0, le=0.50)


class PrivacyConfig(BaseModel):
    allow_cloud_text: bool = True
    exclude_training_providers: bool = False
    excluded_apps: list[str] = Field(default_factory=list)


class ModesConfig(BaseModel):
    default: Literal["clean_only", "clean_plus_improved", "structured_json"] = "clean_only"
    fast_path: bool = True
    fast_path_strictness: Literal["balanced", "aggressive", "off"] = "balanced"
    sentence_punctuation: Literal["auto", "always", "never"] = "auto"
    wrap_json_fence: bool = False


class SchemasConfig(BaseModel):
    structured_json: str = "default"


def default_providers() -> dict[str, ProviderConfig]:
    return {
        "groq": ProviderConfig(
            enabled=True,
            models=ModelAliases(
                fast="llama-3.1-8b-instant",
                quality="llama-3.3-70b-versatile",
            ),
            limits=ProviderLimits(rpm=30, rpd=14400, tpm=6000, tpd=500000),
        ),
        "cerebras": ProviderConfig(
            enabled=True,
            models=ModelAliases(
                fast="llama3.1-8b",
                quality="llama-3.3-70b",
            ),
            limits=ProviderLimits(rpm=30, rpd=14400, tpm=60000, tpd=1000000),
        ),
        "gemini": ProviderConfig(
            enabled=True,
            models=ModelAliases(
                fast="gemini-2.5-flash-lite",
                quality="gemini-2.5-flash-lite",
            ),
            limits=ProviderLimits(rpm=15, rpd=1000, tpm=250000, tpd=1000000),
        ),
        "openrouter": ProviderConfig(
            enabled=True,
            models=ModelAliases(
                fast="meta-llama/llama-3.2-3b-instruct:free",
                quality="meta-llama/llama-3.3-70b-instruct:free",
            ),
            limits=ProviderLimits(rpm=20, rpd=50, tpm=40000, tpd=200000),
        ),
        "github_models": ProviderConfig(
            enabled=False,
            models=ModelAliases(
                fast="gpt-4o-mini",
                quality="gpt-4o",
            ),
            limits=ProviderLimits(rpm=10, rpd=150, tpm=40000, tpd=150000),
        ),
        "mistral": ProviderConfig(
            enabled=False,
            models=ModelAliases(
                fast="mistral-small-latest",
                quality="mistral-small-latest",
            ),
            limits=ProviderLimits(rpm=10, rpd=500, tpm=100000, tpd=500000),
        ),
    }


class CoreSettings(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=default_providers)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    modes: ModesConfig = Field(default_factory=ModesConfig)
    schemas: SchemasConfig = Field(default_factory=SchemasConfig)
    debug_log_text: bool = False


def get_appdata_dir() -> Path:
    """Return %APPDATA%/PromptCore on Windows or ~/.config/PromptCore elsewhere."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "PromptCore"
    else:
        base = Path.home() / ".config" / "PromptCore"
    base.mkdir(parents=True, exist_ok=True)
    return base


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dict into base dict."""
    merged = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}