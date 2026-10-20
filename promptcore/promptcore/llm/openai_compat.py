"""
Unified OpenAI-compatible backend for all 6 cloud presets (Task 3.1, §4.3, §4.8, §4.11).
Supports:
  - Non-streaming and SSE streaming (`stream=True`)
  - `on_first_token` callback for 600 ms TTFT hedging
  - `early_abort_check` hook on the first ~20 chars after `<output>` to abort answering models
  - `json_schema` / `json_object` response formatting
  - Normalized error mapping (429, 401/403, 404, 5xx, timeout)
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from promptcore.llm.base import (
    EarlyAbortCheck,
    LLMAuthError,
    LLMError,
    LLMGuardAbortedError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMResult,
    LLMServerError,
    LLMTimeoutError,
)
from promptcore.llm.net import ConnectionManager
from promptcore.llm.providers import get_preset
from promptcore.secrets import get as get_secret

_OUTPUT_TAG_RE = re.compile(r"</?output>", re.IGNORECASE)


def _strip_output_tags(text: str) -> str:
    """Strip `<output>` and `</output>` wrapper tags if present."""
    cleaned = text.strip()
    if "<output>" in cleaned.lower():
        idx = cleaned.lower().find("<output>")
        cleaned = cleaned[idx + len("<output>") :]
    if "</output>" in cleaned.lower():
        idx = cleaned.lower().rfind("</output>")
        cleaned = cleaned[:idx]
    return _OUTPUT_TAG_RE.sub("", cleaned).strip()


class OpenAICompatibleBackend:
    """Single backend implementation covering all 6 OpenAI-compatible free-tier providers."""

    def __init__(
        self,
        conn_manager: ConnectionManager | None = None,
        key_getter: Callable[[str], str | None] | None = None,
    ) -> None:
        self._key_getter = key_getter or get_secret
        self.conn_manager = conn_manager or ConnectionManager(key_getter=self._key_getter)

    def warmup(self, provider: str) -> bool:
        return self.conn_manager.warmup_provider(provider)

    def healthy(self, provider: str) -> bool:
        return self.conn_manager.healthy(provider)

    def _build_payload(
        self,
        *,
        provider: str,
        model: str,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        stream: bool,
        stop: list[str] | None,
    ) -> dict[str, Any]:
        preset = get_preset(provider)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stop:
            payload["stop"] = stop

        # Reasoning-capable models (e.g. Gemini 2.5 or gpt-oss): disable or minimize thinking overhead (§4.8)
        if preset.supports_reasoning_effort and ("2.5" in model or "gpt-oss" in model or "reasoning" in model):
            payload["reasoning_effort"] = "low"

        # Structured JSON mode selection (§4.8)
        if json_schema is not None:
            if preset.supports_json_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": json_schema.get("title", "response_schema"),
                        "schema": json_schema,
                    },
                }
            elif preset.supports_json_object:
                payload["response_format"] = {"type": "json_object"}

        return payload

    def _raise_for_status(
        self,
        resp: httpx.Response,
        provider: str,
        model: str,
    ) -> None:
        status = resp.status_code
        if status < 400:
            return

        headers = {k.lower(): v for k, v in resp.headers.items()}
        body_preview = ""
        try:
            body_preview = resp.text[:300]
        except Exception:
            pass

        if status == 429:
            retry_raw = headers.get("retry-after", "60")
            try:
                retry_s = float(retry_raw)
            except ValueError:
                retry_s = 60.0
            raise LLMRateLimitError(
                f"Rate limit exceeded on {provider}/{model}: {body_preview}",
                provider=provider,
                model=model,
                retry_after_s=max(1.0, retry_s),
                headers=headers,
            )
        if status in (401, 403):
            raise LLMAuthError(
                f"Authentication failed ({status}) on {provider}: check API key",
                provider=provider,
                model=model,
                status_code=status,
                headers=headers,
            )
        if status == 404:
            raise LLMModelNotFoundError(
                f"Model '{model}' not found (404) on {provider}",
                provider=provider,
                model=model,
                status_code=404,
                headers=headers,
            )
        if status >= 500:
            raise LLMServerError(
                f"Server error ({status}) on {provider}/{model}: {body_preview}",
                provider=provider,
                model=model,
                status_code=status,
                headers=headers,
            )
        raise LLMError(
            f"HTTP {status} from {provider}/{model}: {body_preview}",
            provider=provider,
            model=model,
            status_code=status,
            headers=headers,
        )

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
        norm_provider = provider.strip().lower()
        api_key = self._key_getter(norm_provider)
        if not api_key:
            raise LLMAuthError(f"Missing API key for provider '{norm_provider}'", provider=norm_provider, model=model)

        payload = self._build_payload(
            provider=norm_provider,
            model=model,
            system=system,
            user=user,
            json_schema=json_schema,
            max_tokens=max_tokens,
            stream=stream,
            stop=stop,
        )
        client = self.conn_manager.get_client(norm_provider)
        req_headers = {"Authorization": f"Bearer {api_key}"}

        t0 = time.perf_counter()
        try:
            if stream:
                return self._generate_stream(
                    client=client,
                    payload=payload,
                    req_headers=req_headers,
                    provider=norm_provider,
                    model=model,
                    timeout_s=timeout_s,
                    t0=t0,
                    on_first_token=on_first_token,
                    early_abort_check=early_abort_check,
                    cancel_event=cancel_event,
                )
            else:
                return self._generate_non_stream(
                    client=client,
                    payload=payload,
                    req_headers=req_headers,
                    provider=norm_provider,
                    model=model,
                    timeout_s=timeout_s,
                    t0=t0,
                    on_first_token=on_first_token,
                    cancel_event=cancel_event,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"Timeout after {timeout_s:.2f}s on {norm_provider}/{model}",
                provider=norm_provider,
                model=model,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMServerError(
                f"Network error on {norm_provider}/{model}: {exc}",
                provider=norm_provider,
                model=model,
            ) from exc

    def _generate_non_stream(
        self,
        *,
        client: httpx.Client,
        payload: dict[str, Any],
        req_headers: dict[str, str],
        provider: str,
        model: str,
        timeout_s: float,
        t0: float,
        on_first_token: Callable[[], None] | None,
        cancel_event: threading.Event | None,
    ) -> LLMResult:
        if cancel_event and cancel_event.is_set():
            raise LLMTimeoutError("Cancelled before request dispatch", provider=provider, model=model)

        resp = client.post("/chat/completions", json=payload, headers=req_headers, timeout=timeout_s)
        total_ms = (time.perf_counter() - t0) * 1000.0
        self._raise_for_status(resp, provider, model)
        self.conn_manager.touch(provider)

        if on_first_token:
            on_first_token()

        data = resp.json()
        choices = data.get("choices") or []
        raw_text = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or 0)
        headers = {k.lower(): v for k, v in resp.headers.items()}

        return LLMResult(
            text=_strip_output_tags(raw_text),
            provider=provider,
            model=model,
            ttft_ms=total_ms,
            total_ms=total_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            headers=headers,
        )

    def _generate_stream(
        self,
        *,
        client: httpx.Client,
        payload: dict[str, Any],
        req_headers: dict[str, str],
        provider: str,
        model: str,
        timeout_s: float,
        t0: float,
        on_first_token: Callable[[], None] | None,
        early_abort_check: EarlyAbortCheck | None,
        cancel_event: threading.Event | None,
    ) -> LLMResult:
        ttft_ms: float | None = None
        chunks: list[str] = []
        early_checked = False
        in_tok = 0
        out_tok = 0

        with client.stream("POST", "/chat/completions", json=payload, headers=req_headers, timeout=timeout_s) as resp:
            if resp.status_code >= 400:
                resp.read()
                self._raise_for_status(resp, provider, model)

            headers = {k.lower(): v for k, v in resp.headers.items()}
            self.conn_manager.touch(provider)

            for raw_line in resp.iter_lines():
                if cancel_event and cancel_event.is_set():
                    raise LLMTimeoutError("Stream cancelled by hedge winner", provider=provider, model=model)
                if (time.perf_counter() - t0) > timeout_s:
                    raise LLMTimeoutError(f"Stream exceeded {timeout_s:.2f}s budget", provider=provider, model=model)

                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if "usage" in event and isinstance(event["usage"], dict):
                    in_tok = int(event["usage"].get("prompt_tokens") or in_tok)
                    out_tok = int(event["usage"].get("completion_tokens") or out_tok)

                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if not piece:
                    continue

                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t0) * 1000.0
                    if on_first_token:
                        on_first_token()

                chunks.append(piece)

                # Check early-abort guard once >= 20 chars after <output> have arrived (§4.11)
                if early_abort_check is not None and not early_checked:
                    accumulated = _strip_output_tags("".join(chunks))
                    if len(accumulated) >= 20:
                        early_checked = True
                        if early_abort_check(accumulated[:40]):
                            raise LLMGuardAbortedError(
                                f"Streaming early abort triggered on '{accumulated[:30]}'",
                                provider=provider,
                                model=model,
                            )

        total_ms = (time.perf_counter() - t0) * 1000.0
        full_text = _strip_output_tags("".join(chunks))
        if out_tok == 0 and full_text:
            out_tok = max(1, len(full_text) // 4)

        return LLMResult(
            text=full_text,
            provider=provider,
            model=model,
            ttft_ms=ttft_ms if ttft_ms is not None else total_ms,
            total_ms=total_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            headers=headers,
        )