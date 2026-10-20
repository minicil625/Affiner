"""
Output schemas and rendering helpers for Mode 2 (`CleanImproved`) and Mode 3 (`StructuredPrompt`)
per Section 4.10 and Task 3.4.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CleanImproved(BaseModel):
    """
    Mode 2 schema (§4.10).
    When the local fast path already produced `clean`, only `enhanced` is required from the LLM.
    """

    clean: str = ""
    enhanced: str


class StructuredPrompt(BaseModel):
    """
    Mode 3 schema (§4.10) including `steps: list[str]`.
    """

    goal: str
    context: dict[str, str] = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list)
    constraints: dict[str, str] = Field(default_factory=dict)
    steps: list[str] = Field(default_factory=list)
    expected_output: str = ""


def render_mode2_output(clean: str, enhanced: str) -> str:
    """
    Render Mode 2 output using the exact PRD template (§4.10):
    `{clean}\\n\\n---\\n**Enhanced Prompt:**\\n{enhanced}`
    """
    return f"{clean.strip()}\n\n---\n**Enhanced Prompt:**\n{enhanced.strip()}"


def render_mode3_output(
    structured: StructuredPrompt | dict[str, Any],
    wrap_json_fence: bool = False,
) -> str:
    """Render Mode 3 validated JSON with `indent=2, ensure_ascii=False`."""
    payload = structured.model_dump() if isinstance(structured, StructuredPrompt) else structured
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    if wrap_json_fence:
        return f"```json\n{pretty}\n```"
    return pretty


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """
    Extract and parse a JSON object from `raw_text`, handling optional markdown code fences
    or leading/trailing prose when a provider falls back to plain text.
    """
    text = raw_text.strip()
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: locate outermost `{ ... }`
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Could not parse a valid JSON object from model output")


def load_custom_schema(schema_setting: str) -> dict[str, Any]:
    """Load custom Mode 3 JSON schema if configured, otherwise return StructuredPrompt's schema."""
    if not schema_setting or schema_setting == "default":
        return StructuredPrompt.model_json_schema()
    path = Path(schema_setting)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return StructuredPrompt.model_json_schema()