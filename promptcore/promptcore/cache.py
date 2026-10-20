"""
LRU cache (256 entries) keyed on `(mode, input_type, provider, model, sha1(text))` (§4.13, Task 3.3).
Re-running the same text in the same mode costs 0 free-tier quota and returns in < 1 ms.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Final

DEFAULT_CACHE_CAPACITY: Final[int] = 256


class PromptCache:
    """Thread-safe LRU cache for processed outputs."""

    def __init__(self, capacity: int = DEFAULT_CACHE_CAPACITY) -> None:
        self.capacity = max(1, capacity)
        self._store: OrderedDict[tuple[str, str, str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def get(
        self,
        *,
        mode: str,
        input_type: str,
        text: str,
        provider: str = "*",
        model: str = "*",
    ) -> str | None:
        digest = self._hash_text(text)
        key = (mode, input_type, provider, model, digest)
        wildcard_key = (mode, input_type, "*", "*", digest)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return self._store[key]
            if wildcard_key in self._store:
                self._store.move_to_end(wildcard_key)
                return self._store[wildcard_key]
        return None

    def put(
        self,
        *,
        mode: str,
        input_type: str,
        text: str,
        output: str,
        provider: str = "*",
        model: str = "*",
    ) -> None:
        digest = self._hash_text(text)
        key = (mode, input_type, provider, model, digest)
        wildcard_key = (mode, input_type, "*", "*", digest)
        with self._lock:
            for k in (key, wildcard_key):
                self._store[k] = output
                self._store.move_to_end(k)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()