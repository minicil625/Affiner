"""
Task 0.3 — `promptcore/scripts/check_providers.py`
Verifies configured API keys via `GET /models`, lists free models, measures real TTFT
and total latency (p50/p95) from your network connection on 10 / 50 / 150-word inputs,
writes `%APPDATA%\\PromptCore\\providers.yaml` and `benchmark_results.md`, and prints a
recommended per-mode routing chain.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from promptcore.config import CoreSettings, get_appdata_dir
from promptcore.llm.openai_compat import OpenAICompatibleBackend
from promptcore.llm.providers import PRESETS
from promptcore.prompts.builder import build_system_prompt, build_user_message
from promptcore.prompts.schemas import StructuredPrompt
from promptcore.secrets import get as get_secret

# Sample prompts of ~10, ~50, and ~150 words
BENCH_INPUTS: dict[str, str] = {
    "10w": "pls fix teh connection timeout bug in ⟦0⟧ right now",
    "50w": (
        "we need to refactor the authentication middleware in ⟦0⟧ because right now "
        "it queries sqlite wait actually postgres on every single request instead of caching "
        "the verified JWT session in redis for five minutes and returning a 401 status code "
        "whenever the bearer token is expired or malformed"
    ),
    "150w": (
        "can you help me design a background worker service in python using fastapi and asyncio "
        "that reads pending webhook events from ⟦0⟧ and dispatches them over HTTP/2 with httpx. "
        "First we should make sure each webhook payload is validated with pydantic before sending. "
        "Second if the remote endpoint returns a 429 rate limit or a 503 server error we need "
        "exponential backoff with jitter up to five retries while respecting the retry after header. "
        "Third if a delivery still fails after five attempts move the record into a dead letter queue "
        "table in postgres and log a structured warning without leaking any secret headers. "
        "Finally expose a lightweight healthcheck endpoint at ⟦1⟧ that reports current queue depth, "
        "average delivery latency, and worker task status so prometheus can scrape it every fifteen seconds."
    ),
}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(len(sorted_vals) - 1, max(0, int(round((pct / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def check_and_benchmark(
    settings: CoreSettings | None = None,
    output_md_path: Path | None = None,
) -> dict[str, Any]:
    cfg = settings or CoreSettings()
    backend = OpenAICompatibleBackend()
    report_rows: list[dict[str, Any]] = []
    discovered_providers: dict[str, Any] = {}

    print("=====================================================================")
    print("  PromptCore — Cloud Free-Tier Provider Check & Latency Benchmark")
    print("=====================================================================\n")

    for name, prov_cfg in cfg.providers.items():
        api_key = get_secret(name)
        if not api_key:
            print(f"[-] {name:<14}: Skipped (no API key in 'PromptCore' keyring)")
            continue

        preset = PRESETS.get(name)
        if not preset:
            continue

        client = backend.conn_manager.get_client(name)
        try:
            resp = client.get("/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=5.0)
            if resp.status_code >= 400:
                print(f"[!] {name:<14}: /models failed with HTTP {resp.status_code}")
                continue
            models_data = resp.json().get("data", [])
            model_ids = [m.get("id", "") for m in models_data if isinstance(m, dict) and m.get("id")]
            if name == "openrouter":
                model_ids = [m for m in model_ids if m.endswith(":free")]
            print(f"[✓] {name:<14}: Key verified ({len(model_ids)} models available)")
        except Exception as exc:
            print(f"[!] {name:<14}: Connection error during /models check: {exc}")
            continue

        # Benchmark fast model (Mode 1 streamed) and quality model (Mode 3 JSON)
        for role, model_name, mode, stream in [
            ("fast", prov_cfg.models.fast, "clean_only", True),
            ("quality", prov_cfg.models.quality, "structured_json", False),
        ]:
            ttfts: list[float] = []
            totals: list[float] = []
            tps_list: list[float] = []
            json_ok = True
            last_headers: dict[str, str] = {}

            sys_prompt = build_system_prompt(mode, "typed")  # type: ignore[arg-type]
            schema = StructuredPrompt.model_json_schema() if mode == "structured_json" else None

            for label, sample_text in BENCH_INPUTS.items():
                user_msg = build_user_message(sample_text, "typed", ["PostgreSQL", "FastAPI", "Redis", "Pydantic"])
                try:
                    res = backend.generate(
                        sys_prompt,
                        user_msg,
                        provider=name,
                        model=model_name,
                        json_schema=schema,
                        max_tokens=220 if mode == "clean_only" else 350,
                        timeout_s=4.5,
                        stream=stream,
                        stop=["</output>"] if mode == "clean_only" else None,
                    )
                    ttfts.append(res.ttft_ms)
                    totals.append(res.total_ms)
                    if res.total_ms > 0 and res.output_tokens > 0:
                        tps_list.append(res.output_tokens / (res.total_ms / 1000.0))
                    last_headers = res.headers
                except Exception as exc:
                    print(f"    [!] {name}.{role} ({model_name}) failed on {label}: {exc}")
                    if mode == "structured_json":
                        json_ok = False

            if totals:
                p50_ms = statistics.median(totals)
                p95_ms = _percentile(totals, 95.0)
                ttft_p50 = statistics.median(ttfts)
                avg_tps = statistics.mean(tps_list) if tps_list else 0.0
                print(
                    f"    -> {name}.{role:<7} ({model_name}): "
                    f"TTFT p50={ttft_p50:.0f}ms | Total p50={p50_ms:.0f}ms p95={p95_ms:.0f}ms | {avg_tps:.0f} tok/s"
                )
                report_rows.append(
                    {
                        "provider": name,
                        "role": role,
                        "model": model_name,
                        "mode": mode,
                        "ttft_p50_ms": round(ttft_p50, 1),
                        "total_p50_ms": round(p50_ms, 1),
                        "total_p95_ms": round(p95_ms, 1),
                        "tok_per_s": round(avg_tps, 1),
                        "json_ok": json_ok,
                    }
                )

        discovered_providers[name] = {
            "enabled": True,
            "models": {
                "fast": prov_cfg.models.fast,
                "quality": prov_cfg.models.quality,
            },
            "limits": prov_cfg.limits.model_dump(),
        }

    appdata_providers_path = get_appdata_dir() / "providers.yaml"
    if discovered_providers:
        appdata_providers_path.write_text(
            yaml.safe_dump({"providers": discovered_providers}, sort_keys=False),
            encoding="utf-8",
        )
        print(f"\n[✓] Saved verified provider configuration to: {appdata_providers_path}")

    md_path = output_md_path or Path("benchmark_results.md")
    lines = [
        "# Cloud Free-Tier Provider Benchmark Results",
        "",
        f"Generated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "| Provider | Alias | Model | Mode | TTFT p50 (ms) | Total p50 (ms) | Total p95 (ms) | Tokens/s |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for r in report_rows:
        lines.append(
            f"| `{r['provider']}` | `{r['role']}` | `{r['model']}` | `{r['mode']}` | "
            f"{r['ttft_p50_ms']} | {r['total_p50_ms']} | {r['total_p95_ms']} | {r['tok_per_s']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[✓] Wrote benchmark report to: {md_path.resolve()}")

    return {"rows": report_rows, "providers_yaml": str(appdata_providers_path)}


if __name__ == "__main__":
    check_and_benchmark()