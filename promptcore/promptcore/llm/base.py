"""
LLMBackend protocol, LLMResult container, and normalized exception types (§4.1, §4.5).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResult:
    """Normalized result returned by any LLM backend or RuleBackend."""

    text: str
    provider: str
    model: str
    ttft_ms: float = 0.0
    total_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    hedged: bool = False


class LLMError(Exception):
    """Base exception for all LLM backend failures."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.headers = headers or {}


class LLMRateLimitError(LLMError):
    """HTTP 429 rate limit or quota exceeded."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        model: str = "",
        retry_after_s: float = 60.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message, provider=provider, model=model, status_code=429, headers=headers)
        self.retry_after_s = retry_after_s


class LLMAuthError(LLMError):
    """HTTP 401 / 403 invalid or unauthorized API key."""


class LLMModelNotFoundError(LLMError):
    """HTTP 404 model unavailable or deprecated."""


class LLMServerError(LLMError):
    """HTTP 5xx or transient network error."""


class LLMTimeoutError(LLMError):
    """Request exceeded per-mode timeout budget."""


class LLMGuardAbortedError(LLMError):
    """Streaming early-abort or post-generation guard rejected output."""


EarlyAbortCheck = Callable[[str], bool]


class LLMBackend(Protocol):
    """Protocol implemented by OpenAICompatibleBackend and RuleBackend."""

    def generate(
        self,
        system: str,
        user: str,
        *,
        provider: str,
        model: str,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 256,
        timeout_s: float = 1.5,
        stream: bool = False,
        stop: list[str] | None = None,
        on_first_token: Callable[[], None] | None = None,
        early_abort_check: EarlyAbortCheck | None = None,
        cancel_event: threading.Event | None = None,
    ) -> LLMResult:
        ...

    def warmup(self, provider: str) -> bool:
        ...

    def healthy(self, provider: str) -> bool:
        ...