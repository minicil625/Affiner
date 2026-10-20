"""
Lightweight, zero-dependency audio cues (< 60 ms WAV chimes in memory) played
asynchronously via `winsound.PlaySound(..., SND_ASYNC | SND_MEMORY)` (Task 5.4).
"""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from functools import lru_cache
from typing import Final

IS_WINDOWS: Final[bool] = sys.platform == "win32"

if IS_WINDOWS:
    import winsound


@lru_cache(maxsize=8)
def _build_sine_wav(freq_hz: float, duration_ms: int = 55, volume: float = 0.22) -> bytes:
    """Synthesize a tiny mono 16-bit PCM WAV with soft fade-in/out envelope."""
    sample_rate = 22050
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_samples):
            # Hann window envelope to prevent clicks
            env = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / max(1, num_samples - 1)))
            sample = math.sin(2.0 * math.pi * freq_hz * (i / sample_rate)) * env * volume
            frames.extend(struct.pack("<h", int(sample * 32767)))
        wf.writeframes(bytes(frames))
    return buf.getvalue()


class SoundPlayer:
    """Plays non-blocking <60 ms audio cues according to `UIConfig`."""

    MODE_FREQS: Final[dict[str, float]] = {
        "clean_only": 587.33,           # D5
        "clean_plus_improved": 739.99,  # F#5
        "structured_json": 880.00,      # A5
    }

    def __init__(self, enabled: bool = True, done_sound: bool = False) -> None:
        self.enabled = enabled
        self.done_sound = done_sound

    def _play_bytes(self, wav_bytes: bytes) -> None:
        if not self.enabled or not IS_WINDOWS:
            return
        try:
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass

    def play_mode_switch(self, mode: str) -> None:
        freq = self.MODE_FREQS.get(mode, 659.25)
        self._play_bytes(_build_sine_wav(freq, duration_ms=55, volume=0.20))

    def play_done(self) -> None:
        if not self.done_sound:
            return
        self._play_bytes(_build_sine_wav(783.99, duration_ms=45, volume=0.16))

    def play_error(self) -> None:
        self._play_bytes(_build_sine_wav(293.66, duration_ms=70, volume=0.25))