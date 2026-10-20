"""
Multi-provider LLM router (§4.4, §4.5, Task 3.2).
Features:
  - Filters candidates by enabled flag, API key presence, 401/403 auth state, 404 model state,
    privacy settings (`allow_cloud_text`, `exclude_training_providers`), cooldowns, and quota margin.
  - Ranks candidates by latency EMA (alpha = 0.3; TTFT for streamed modes, total_ms otherwise)
    once a provider has >= 5 samples; uses config order until then.
  - Supports first-token hedging (Mode 1: 600 ms) and full-response hedging (Modes 2/3 when enabled).
  - Handles 429 cooldowns (`retry-after` or 60s), 5xx/timeout cooldowns (30s), 401/403 disabling,
    404 model marking, and guard soft-failures.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

from promptcore.config import CoreSettings, ModeRouteConfig
from promptcore.llm.base import (
    EarlyAbortCheck,
    LLMAuthError,
    LLMBackend,
    LLMError,
    LLMGuardAbortedError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMResult,
    LLMServerError,
    LLMTimeoutError,
)
from promptcore.llm.openai_compat import OpenAICompatibleBackend
from promptcore.llm.providers import PRESETS
from promptcore.llm.rate_limiter import RateLimiter
from promptcore.secrets import get as get_secret

logger = logging.getLogger(__name__)

EMA_ALPHA: float = 0.3
MIN_SAMPLES_FOR_RANKING: int = 5


@dataclass(frozen=True)
class RouteCandidate:
    provider: str
    alias: str          # "fast" | "quality" or explicit model name
    model: str
    config_index: int


class LLMRouter:
    """
    Routes LLM requests across free-tier providers with latency ranking, hedging, and failover.
    """

    def __init__(
        self,
        settings: CoreSettings,
        backend: LLMBackend | None = None,
        rate_limiter: RateLimiter | None = None,
        key_getter: Callable[[str], str | None] | None = None,
        on_notice: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._key_getter = key_getter or get_secret
        self.backend = backend or OpenAICompatibleBackend(key_getter=self._key_getter)
        limits_map = {name: cfg.limits for name, cfg in settings.providers.items()}
        self.rate_limiter = rate_limiter or RateLimiter(
            limits_by_provider=limits_map,
            safety_margin=settings.routing.quota_safety_margin,
        )
        self.on_notice = on_notice

        self._lock = threading.Lock()
        self._cooldown_until: dict[str, float] = {}
        self._disabled_auth: set[str] = set()
        self._unavailable_models: set[tuple[str, str]] = set()
        # Latency EMA tracking: (provider, streamed_bool) -> (ema_ms, sample_count)
        self._latency_ema: dict[tuple[str, bool], tuple[float, int]] = {}

    def _emit_notice(self, message: str) -> None:
        logger.warning("Router notice: %s", message)
        if self.on_notice:
            try:
                self.on_notice(message)
            except Exception:
                pass

    def _resolve_chain_entry(self, entry: str, idx: int) -> RouteCandidate | None:
        if "." not in entry:
            return None
        provider, alias = entry.split(".", 1)
        provider = provider.strip().lower()
        alias = alias.strip()
        prov_cfg = self.settings.providers.get(provider)
        if prov_cfg is None:
            return None
        if alias == "fast":
            model = prov_cfg.models.fast
        elif alias == "quality":
            model = prov_cfg.models.quality
        else:
            model = alias
        return RouteCandidate(provider=provider, alias=alias, model=model, config_index=idx)

    def get_candidates(
        self,
        mode: str,
        estimated_tokens: int = 250,
    ) -> list[RouteCandidate]:
        """
        Return the filtered and latency-ranked candidate list for `mode` (§4.5).
        """
        if not self.settings.privacy.allow_cloud_text:
            return []

        route_cfg: ModeRouteConfig = getattr(self.settings.routing, mode)
        now = time.monotonic()
        valid: list[RouteCandidate] = []

        with self._lock:
            for idx, entry in enumerate(route_cfg.chain):
                cand = self._resolve_chain_entry(entry, idx)
                if cand is None:
                    continue
                prov_cfg = self.settings.providers.get(cand.provider)
                if not prov_cfg or not prov_cfg.enabled:
                    continue
                if cand.provider in self._disabled_auth:
                    continue
                if (cand.provider, cand.model) in self._unavailable_models:
                    continue
                preset = PRESETS.get(cand.provider)
                if (
                    preset
                    and self.settings.privacy.exclude_training_providers
                    and preset.trains_on_free_data
                ):
                    continue
                cooldown_exp = self._cooldown_until.get(cand.provider, 0.0)
                if now < cooldown_exp:
                    continue
                if not self._key_getter(cand.provider):
                    continue
                if not self.rate_limiter.has_capacity(
                    cand.provider,
                    estimated_tokens=estimated_tokens,
                    safety_margin=self.settings.routing.quota_safety_margin,
                ):
                    continue
                valid.append(cand)

            if not self.settings.routing.latency_ranking or len(valid) <= 1:
                return valid

            # Only re-rank candidates that have >= 5 latency samples; preserve config order otherwise
            stream_flag = route_cfg.stream

            def _sort_key(c: RouteCandidate) -> tuple[int, float, int]:
                ema_info = self._latency_ema.get((c.provider, stream_flag))
                if ema_info is not None and ema_info[1] >= MIN_SAMPLES_FOR_RANKING:
                    # Group 0: ranked by measured EMA
                    return (0, round(ema_info[0], 1), c.config_index)
                # Group 1: fewer than 5 samples, keep after or in config order
                return (1, float(c.config_index), c.config_index)

            # If all candidates have >= 5 samples, sort by EMA; if only some have >= 5 samples,
            # compare measured ones fairly while keeping unmeasured ones in config order
            all_measured = all(
                self._latency_ema.get((c.provider, stream_flag), (0.0, 0))[1] >= MIN_SAMPLES_FOR_RANKING
                for c in valid
            )
            if all_measured:
                valid.sort(key=_sort_key)
            else:
                # Sort subset of measured candidates among their positions if >= 2 are measured
                measured_Count = sum(
                    1
                    for c in valid
                    if self._latency_ema.get((c.provider, stream_flag), (0.0, 0))[1] >= MIN_SAMPLES_FOR_RANKING
                )
                if measured_Count >= 2:
                    valid.sort(key=_sort_key)

        return valid

    def _record_latency(self, provider: str, stream: bool, metric_ms: float) -> None:
        with self._lock:
            key = (provider, stream)
            prev = self._latency_ema.get(key)
            if prev is None:
                self._latency_ema[key] = (metric_ms, 1)
            else:
                old_ema, count = prev
                new_ema = EMA_ALPHA * metric_ms + (1.0 - EMA_ALPHA) * old_ema
                self._latency_ema[key] = (new_ema, count + 1)

    def _handle_provider_error(self, exc: LLMError) -> None:
        now = time.monotonic()
        with self._lock:
            if isinstance(exc, LLMRateLimitError):
                self._cooldown_until[exc.provider] = now + max(1.0, exc.retry_after_s)
            elif isinstance(exc, (LLMServerError, LLMTimeoutError)):
                self._cooldown_until[exc.provider] = now + 30.0
            elif isinstance(exc, LLMAuthError):
                self._disabled_auth.add(exc.provider)
                self._emit_notice(f"Check API key for {exc.provider}")
            elif isinstance(exc, LLMModelNotFoundError):
                self._unavailable_models.add((exc.provider, exc.model))
                self._emit_notice(
                    f"Model {exc.model} unavailable on {exc.provider}; run check_providers.py"
                )

    def route(
        self,
        mode: str,
        system: str,
        user: str,
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 256,
        stop: list[str] | None = None,
        early_abort_check: EarlyAbortCheck | None = None,
        post_guard: Callable[[LLMResult], bool] | None = None,
    ) -> LLMResult:
        """
        Execute `mode` across the candidate chain with hedging, failover, and post-generation guards.
        Raises `LLMError` if all candidates fail or time out so `PromptEngine` can run final fallback.
        """
        route_cfg: ModeRouteConfig = getattr(self.settings.routing, mode)
        total_budget_s = route_cfg.timeout_ms / 1000.0
        est_tokens = max(64, (len(system) + len(user)) // 4 + max_tokens)

        candidates = self.get_candidates(mode, estimated_tokens=est_tokens)
        if not candidates:
            raise LLMError(f"No available cloud providers for mode '{mode}'")

        t_start = time.perf_counter()
        last_error: Exception | None = None
        idx = 0

        while idx < len(candidates):
            elapsed_s = time.perf_counter() - t_start
            remaining_s = total_budget_s - elapsed_s
            if remaining_s <= 0.05:
                break

            primary = candidates[idx]
            can_hedge = (
                route_cfg.hedge.enabled
                and (idx + 1) < len(candidates)
                and remaining_s > (route_cfg.hedge.after_ms / 1000.0 + 0.10)
            )
            secondary: RouteCandidate | None = None
            if can_hedge:
                backup_cand = candidates[idx + 1]
                # §4.5: No hedge if the next provider is below 2x the quota safety margin
                if self.rate_limiter.has_capacity(
                    backup_cand.provider,
                    estimated_tokens=est_tokens,
                    safety_margin=min(0.45, self.settings.routing.quota_safety_margin * 2.0),
                ):
                    secondary = backup_cand

            if secondary is not None:
                idx += 2
                try:
                    res = self._call_with_hedge(
                        primary=primary,
                        secondary=secondary,
                        route_cfg=route_cfg,
                        system=system,
                        user=user,
                        json_schema=json_schema,
                        max_tokens=max_tokens,
                        stop=stop,
                        remaining_s=remaining_s,
                        early_abort_check=early_abort_check,
                    )
                except LLMError as exc:
                    last_error = exc
                    continue
            else:
                idx += 1
                try:
                    res = self._call_single(
                        candidate=primary,
                        route_cfg=route_cfg,
                        system=system,
                        user=user,
                        json_schema=json_schema,
                        max_tokens=max_tokens,
                        stop=stop,
                        timeout_s=remaining_s,
                        early_abort_check=early_abort_check,
                    )
                except LLMError as exc:
                    self._handle_provider_error(exc)
                    last_error = exc
                    continue

            # Run post-generation guard if provided (§4.5: soft failure tries next provider if >= 300ms left)
            if post_guard is not None and not post_guard(res):
                last_error = LLMGuardAbortedError(
                    f"Post-generation guard failed on {res.provider}/{res.model}",
                    provider=res.provider,
                    model=res.model,
                )
                if (total_budget_s - (time.perf_counter() - t_start)) >= 0.30:
                    continue
                raise last_error

            return res

        if isinstance(last_error, LLMError):
            raise last_error
        raise LLMTimeoutError(f"All candidates exhausted or timed out for mode '{mode}'")

    def _call_single(
        self,
        *,
        candidate: RouteCandidate,
        route_cfg: ModeRouteConfig,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        stop: list[str] | None,
        timeout_s: float,
        early_abort_check: EarlyAbortCheck | None,
        on_first_token: Callable[[], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> LLMResult:
        res = self.backend.generate(
            system,
            user,
            provider=candidate.provider,
            model=candidate.model,
            json_schema=json_schema,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            stream=route_cfg.stream,
            stop=stop,
            on_first_token=on_first_token,
            early_abort_check=early_abort_check,
            cancel_event=cancel_event,
        )
        # Record usage and latency EMA
        self.rate_limiter.record_usage(
            provider=res.provider,
            model=res.model,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            headers=res.headers,
        )
        metric = res.ttft_ms if route_cfg.stream else res.total_ms
        self._record_latency(res.provider, route_cfg.stream, metric)
        return res

    def _call_with_hedge(
        self,
        *,
        primary: RouteCandidate,
        secondary: RouteCandidate,
        route_cfg: ModeRouteConfig,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None,
        max_tokens: int,
        stop: list[str] | None,
        remaining_s: float,
        early_abort_check: EarlyAbortCheck | None,
    ) -> LLMResult:
        """
        Launch `primary`. If `hedge.trigger == "first_token"`, wait `after_ms` for `first_token_event`.
        If `hedge.trigger == "full_response"`, wait `after_ms` for primary completion.
        If the trigger hasn't fired in time, launch `secondary` in parallel and return the first winner.
        """
        hedge_delay_s = route_cfg.hedge.after_ms / 1000.0
        trigger_event = threading.Event()
        cancel_primary = threading.Event()
        cancel_secondary = threading.Event()

        def _run_primary() -> LLMResult:
            return self._call_single(
                candidate=primary,
                route_cfg=route_cfg,
                system=system,
                user=user,
                json_schema=json_schema,
                max_tokens=max_tokens,
                stop=stop,
                timeout_s=remaining_s,
                early_abort_check=early_abort_check,
                on_first_token=trigger_event.set if route_cfg.hedge.trigger == "first_token" else None,
                cancel_event=cancel_primary,
            )

        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-hedge")
        try:
            fut_primary: Future[LLMResult] = executor.submit(_run_primary)
            if route_cfg.hedge.trigger == "full_response":
                fut_primary.add_done_callback(lambda _: trigger_event.set())
            else:
                # Also wake up immediately if primary fails before first token
                fut_primary.add_done_callback(lambda _: trigger_event.set())

            fired_in_time = trigger_event.wait(timeout=hedge_delay_s)

            # Case 1: Primary already finished (either succeeded or raised fast error before hedge_delay_s)
            if fut_primary.done():
                try:
                    return fut_primary.result()
                except LLMError as exc:
                    self._handle_provider_error(exc)
                    # Immediately fail over to secondary without waiting
                    return self._call_single(
                        candidate=secondary,
                        route_cfg=route_cfg,
                        system=system,
                        user=user,
                        json_schema=json_schema,
                        max_tokens=max_tokens,
                        stop=stop,
                        timeout_s=max(0.1, remaining_s - hedge_delay_s),
                        early_abort_check=early_abort_check,
                    )

            # Case 2: First token arrived in time during streaming -> wait for primary to finish
            if fired_in_time and route_cfg.hedge.trigger == "first_token":
                try:
                    return fut_primary.result(timeout=max(0.1, remaining_s - hedge_delay_s))
                except Exception as exc:
                    cancel_primary.set()
                    if isinstance(exc, LLMError):
                        self._handle_provider_error(exc)
                    return self._call_single(
                        candidate=secondary,
                        route_cfg=route_cfg,
                        system=system,
                        user=user,
                        json_schema=json_schema,
                        max_tokens=max_tokens,
                        stop=stop,
                        timeout_s=max(0.1, remaining_s - hedge_delay_s),
                        early_abort_check=early_abort_check,
                    )

            # Case 3: Hedge delay elapsed without first token / full response -> launch secondary in parallel!
            rem_after_hedge = max(0.1, remaining_s - hedge_delay_s)

            def _run_secondary() -> LLMResult:
                res = self._call_single(
                    candidate=secondary,
                    route_cfg=route_cfg,
                    system=system,
                    user=user,
                    json_schema=json_schema,
                    max_tokens=max_tokens,
                    stop=stop,
                    timeout_s=rem_after_hedge,
                    early_abort_check=early_abort_check,
                    cancel_event=cancel_secondary,
                )
                res.hedged = True
                return res

            fut_secondary: Future[LLMResult] = executor.submit(_run_secondary)
            pending: set[Future[LLMResult]] = {fut_primary, fut_secondary}
            last_err: LLMError | None = None

            while pending:
                done, pending = wait(pending, timeout=rem_after_hedge, return_when=FIRST_COMPLETED)
                if not done:
                    cancel_primary.set()
                    cancel_secondary.set()
                    raise LLMTimeoutError("Hedged requests timed out", provider=primary.provider, model=primary.model)

                for d in done:
                    try:
                        winner = d.result()
                        if d is fut_primary:
                            cancel_secondary.set()
                        else:
                            cancel_primary.set()
                        return winner
                    except LLMError as exc:
                        self._handle_provider_error(exc)
                        last_err = exc
                    except Exception as exc:
                        last_err = LLMServerError(str(exc), provider=primary.provider, model=primary.model)

            if last_err is not None:
                raise last_err
            raise LLMTimeoutError("Both hedged providers failed", provider=primary.provider, model=primary.model)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)