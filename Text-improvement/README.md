# TextPrompt AI (Cloud Free-Tier Edition)

**TextPrompt AI** is an ultra-lightweight Windows utility (`< 1%` idle CPU, `< 150 MB` RAM, **$0 cost**) that captures selected text in any application, cleans or structures it using a `< 10 ms` local deterministic rule + SymSpell engine and free-tier cloud LLMs, and replaces the selection in place while preserving your clipboard.

It shares its core engine (**`promptcore`**), API keys (Windows Credential Manager service **`PromptCore`**), and daily free-tier quota tracker (`%APPDATA%\PromptCore\usage.json`) with its sister project, **VoicePrompt AI**.

---

## 1. Quick Start & Installation

### Prerequisites
- Windows 10 or Windows 11
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (automatically installed by `setup.ps1` if missing)

### One-Command Setup
From PowerShell inside `Text-improvement/`:

```powershell
.\scripts\setup.ps1
```

This script:
1. Syncs the `uv` workspace (linking `../promptcore` as an editable dependency).
2. Downloads the MIT SymSpell English frequency dictionary into `data/`.
3. Launches an interactive wizard to store your free API keys in Windows Credential Manager under service **`PromptCore`**.

### Verify Providers & Measure Your Network Latency
```powershell
uv run python ../promptcore/scripts/check_providers.py
```
This verifies each key via `GET /models`, benchmarks real TTFT and p50/p95 latency from your connection, writes `%APPDATA%\PromptCore\providers.yaml`, and generates `benchmark_results.md`.

### Run TextPrompt AI
```powershell
uv run textprompt
```

---

## 2. Getting Free-Tier API Keys ($0 / No Credit Card)

Enter keys once in `setup.ps1` or via **System Tray → Manage API Keys…** and both TextPrompt AI and VoicePrompt AI will use them automatically:

| Provider | Role | Sign-Up Link | Free-Tier Notes |
|---|---|---|---|
| **Groq** | Primary Fast (8B) & Quality (70B) | `https://console.groq.com/keys` | ~14,400 RPD on `llama-3.1-8b-instant` |
| **Cerebras** | High-speed Fallback | `https://cloud.cerebras.ai/` | Ultra-low TTFT wafer-scale inference |
| **Google AI Studio** | Primary Mode 3 JSON (`gemini-2.5-flash-lite`) | `https://aistudio.google.com/apikey` | Create key in a project with **no billing enabled** |
| **OpenRouter** | Last Cloud Fallback (`:free` models) | `https://openrouter.ai/keys` | Enable privacy settings to block training providers |
| **GitHub Models** | Optional (`disabled` by default) | `https://github.com/settings/tokens` | Uses standard GitHub Personal Access Token |
| **Mistral** | Optional (`disabled` by default) | `https://console.mistral.ai/api-keys/` | Free Experiment tier |

---

## 3. Global Hotkey Cheat-Sheet (Coexists with VoicePrompt AI)

Both applications can run simultaneously without shortcut collisions:

| Action | TextPrompt AI (Selected Text) | VoicePrompt AI (Microphone) |
|---|---|---|
| **Run Active Mode** | `Ctrl+Alt+Space` | `Ctrl+Win` (hold) / `Ctrl+Alt+Shift+Space` |
| **Mode 1: Clean Only** | `Ctrl+Alt+1` (one-shot run) | `Ctrl+Alt+Shift+1` (set active mode) |
| **Mode 2: Clean + Improved** | `Ctrl+Alt+2` (one-shot run) | `Ctrl+Alt+Shift+2` (set active mode) |
| **Mode 3: Structured JSON** | `Ctrl+Alt+3` (one-shot run) | `Ctrl+Alt+Shift+3` (set active mode) |
| **Cycle Active Mode** | `Ctrl+Alt+M` | `Ctrl+Alt+Shift+M` |
| **Undo / Re-run** | `Ctrl+Alt+Z` (undo last replace) | `Ctrl+Alt+Shift+R` (re-run last transcript) |

---

## 4. The 3 Refinement Modes (with Examples)

### Mode 1 — Clean Only (`clean_only`, Default)
Fixes typos, shorthand, spoken syntax, filler words, self-corrections, and technical casing while keeping your exact intent and structure. Simple edits take the **Local Fast Path (`< 10 ms`, 0 API calls)**.

- **Input:** `um pls fix teh bug in app dot py w/ fastapi thx`
- **Output:** `Please fix the bug in app.py with FastAPI, thanks.` *(Local Fast Path — 0.04 s)*

- **Input:** `git checkout dash b feature slash login`
- **Output:** `` `git checkout -b feature/login` `` *(Local Fast Path — 0.03 s)*

### Mode 2 — Clean + Improved (`clean_plus_improved`)
Outputs both your cleaned text and a concise, actionable **Enhanced Prompt** ($\le 140$ words) ready for AI coding assistants (Cursor, Windsurf, Copilot, Claude, ChatGPT).

- **Input:** `pls fix the memory leak in `ws_handler.py` when client disconnects`
- **Output:**
  ```markdown
  Please fix the memory leak in `ws_handler.py` when the client disconnects.

  ---
  **Enhanced Prompt:**
  Identify and resolve the memory leak in `ws_handler.py` triggered when a WebSocket client disconnects. Ensure all active subscriptions, background tasks, and session references are deterministically cleaned up in a `finally` block, and add a unit test verifying resource cleanup on disconnect.
  ```

### Mode 3 — Structured JSON (`structured_json`)
Converts natural language requests into a schema-validated JSON specification (`goal`, `context`, `requirements`, `constraints`, `steps`, `expected_output`).

- **Input:** `build a rate limiter in `limiter.py` using Redis no wait use an in-memory token bucket with filelock`
- **Output:**
  ```json
  {
    "goal": "Implement a thread-safe in-memory token-bucket rate limiter in `limiter.py` with filelock persistence.",
    "context": {
      "target_file": "`limiter.py`",
      "concurrency": "filelock"
    },
    "requirements": [
      "Use token-bucket algorithm",
      "Persist shared state using filelock"
    ],
    "constraints": {
      "external_services": "Do not use Redis"
    },
    "steps": [
      "Define token bucket state",
      "Add filelock synchronization",
      "Integrate into `limiter.py`"
    ],
    "expected_output": "Updated implementation in `limiter.py`"
  }
  ```

---

## 5. Privacy & Security Controls

1. **Local Fast Path:** Over 60% of everyday Mode 1 edits (typos, shorthand, spoken filenames/CLI flags, tech casing) are handled locally on your PC in `< 10 ms` and never sent to any cloud API.
2. **Exclude Training Providers:** Enable `privacy.exclude_training_providers: true` in `config.yaml` (or via the System Tray **Privacy** menu) to automatically skip free tiers that may train on prompts (such as unpaid Google AI Studio or Mistral Experiment).
3. **Excluded Apps (`excluded_apps`):** Password managers (`KeePassXC.exe`, `1Password.exe`, `Bitwarden.exe`) are excluded by default—triggers inside those windows run strictly offline.
4. **Local Rules-Only Mode:** Uncheck **Allow Cloud Text Processing** in the System Tray to force 100% offline deterministic execution across the entire OS.
5. **Zero Text Logging:** Selected text is never written to `%APPDATA%\TextPrompt\logs\textprompt.log` unless you explicitly set `debug_log_text: true`. API keys are automatically redacted from all logs.

---

## 6. Customizing Vocabulary & Shorthand (Hot-Reloaded)

Edit `vocabulary.txt` or `shorthand.txt` (or use **System Tray → Add Word to Vocabulary…**). `watchdog` hot-reloads changes immediately without restarting:

- **`vocabulary.txt`:** Add canonical terms (`Tailscale`) or alias mappings (`pg => PostgreSQL`).
- **`shorthand.txt`:** Add whole-word typed abbreviations (`lgtm => looks good to me`).

---

## 7. Troubleshooting

- **`AltGr` Keyboard Layouts:** On European/International layouts where `Right Alt` acts as `Ctrl+Alt`, remap `hotkeys` in `config.yaml` to use `ctrl+shift+...` or `alt+shift+...`.
- **Windows Terminal / PowerShell / Alacritty:** `config.yaml` automatically maps terminals to `ctrl+shift+c` and `ctrl+shift+v` so `Ctrl+C` never sends `SIGINT` to your running process.
- **Administrator Windows (Task Manager, elevated PowerShell):** Windows UIPI blocks non-elevated apps from injecting `SendInput` into elevated windows. TextPrompt AI detects this and displays *"Run TextPrompt as admin to use in this window"*.
- **HTTP 401 / 429 Errors:** On `429`, the router automatically cools down that provider and fails over to the next provider in `< 50 ms`. On `401`, open **Manage API Keys…** in the System Tray to update the key.