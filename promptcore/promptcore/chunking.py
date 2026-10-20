"""
Long-input chunking for Mode 1 (§4.13, Task 3.3):
Inputs > 150 words are split into ~80-150-word chunks on paragraph boundaries first,
then sentence boundaries (for speech transcripts that lack paragraphs), never splitting
inside a masked `⟦n⟧` span. Runs up to 3 chunks in flight and rejoins in order.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

CHUNK_TRIGGER_WORDS: int = 150
TARGET_CHUNK_MAX_WORDS: int = 130
MAX_IN_FLIGHT_CHUNKS: int = 3

# Sentence boundary regex that avoids splitting inside placeholders
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z⟦\"'])")


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def split_into_chunks(
    text: str,
    trigger_words: int = CHUNK_TRIGGER_WORDS,
    max_chunk_words: int = TARGET_CHUNK_MAX_WORDS,
) -> list[tuple[str, str]]:
    """
    If `text` has <= `trigger_words` words, return `[(text, "")]`.
    Otherwise split into `[(chunk_text, trailing_separator), ...]` of ~80-150 words
    on paragraph breaks (`\\n\\n`) first, then sentence boundaries.
    """
    if _word_count(text) <= trigger_words:
        return [(text, "")]

    # 1. Split by paragraph boundaries (\n\n+)
    raw_parts = re.split(r"(\n\s*\n)", text)
    segments: list[tuple[str, str]] = []
    i = 0
    while i < len(raw_parts):
        seg = raw_parts[i]
        sep = raw_parts[i + 1] if (i + 1) < len(raw_parts) else ""
        i += 2
        if _word_count(seg) > max_chunk_words:
            # Split oversized paragraph on sentence boundaries
            sentences = _SENTENCE_SPLIT_RE.split(seg)
            for s_idx, sent in enumerate(sentences):
                s_sep = " " if s_idx < len(sentences) - 1 else sep
                segments.append((sent, s_sep))
        else:
            segments.append((seg, sep))

    # 2. Pack segments into ~80-150-word chunks
    packed: list[tuple[str, str]] = []
    cur_text = ""
    cur_sep = ""
    cur_words = 0

    for seg, sep in segments:
        seg_words = _word_count(seg)
        if cur_words > 0 and (cur_words + seg_words) > max_chunk_words:
            packed.append((cur_text, cur_sep))
            cur_text = seg
            cur_sep = sep
            cur_words = seg_words
        else:
            cur_text = f"{cur_text}{cur_sep}{seg}" if cur_text else seg
            cur_sep = sep
            cur_words += seg_words

    if cur_text:
        packed.append((cur_text, cur_sep))

    return packed if packed else [(text, "")]


def process_chunks_parallel(
    chunks: list[tuple[str, str]],
    worker_fn: Callable[[str], str],
    max_workers: int = MAX_IN_FLIGHT_CHUNKS,
) -> str:
    """
    Process each chunk with `worker_fn` (up to `max_workers` in flight) and rejoin in order.
    """
    if len(chunks) == 1:
        return worker_fn(chunks[0][0])

    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks)), thread_name_prefix="chunk") as pool:
        futures = [pool.submit(worker_fn, chunk_text) for chunk_text, _ in chunks]
        results = [f.result() for f in futures]

    out_pieces: list[str] = []
    for idx, cleaned in enumerate(results):
        sep = chunks[idx][1]
        out_pieces.append(cleaned + sep)
    return "".join(out_pieces).strip()