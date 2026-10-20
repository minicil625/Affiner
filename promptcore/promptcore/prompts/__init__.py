"""
Static system prompts, few-shot examples, and Pydantic v2 output schemas (§4.9, §4.10).
"""

from promptcore.prompts.builder import build_system_prompt, build_user_message
from promptcore.prompts.schemas import (
    CleanImproved,
    StructuredPrompt,
    extract_json_object,
    render_mode2_output,
)

__all__ = [
    "CleanImproved",
    "StructuredPrompt",
    "build_system_prompt",
    "build_user_message",
    "extract_json_object",
    "render_mode2_output",
]