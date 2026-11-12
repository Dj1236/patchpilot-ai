"""Least-recently-used cache with an optional time-to-live."""

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any


class LRUCache:
    """Hold at most ``capacity`` entries, evicting the least recently used.

    Reading or writing an entry counts as using it. When ``ttl`` is given,
    an entry expires that many seconds after it was last written.
    """

    def __init__(
        self,
        capacity: int,
        ttl: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[Any, float | None]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        self._purge_expired()
        return len(self._entries)

    def __contains__(self, key: Hashable) -> bool:
        entry = self._entries.get(key)
        return entry is not None and not self._is_expired(entry)

    def keys(self) -> list[Hashable]:
        """Return live keys from least to most recently used."""
        self._purge_expired()
        return list(self._entries)

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return the value for ``key`` and mark it as most recently used."""
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return default
        if self._is_expired(entry):
            del self._entries[key]
            self.misses += 1
            return default
        self._entries.move_to_end(key)
        self.hits += 1
        return entry[0]

    def put(self, key: Hashable, value: Any) -> None:
        """Store ``value`` under ``key`` and mark it as most recently used."""
        expires_at = None if self._ttl is None else self._clock() + self._ttl
        if key in self._entries:
            self._entries[key] = (value, expires_at)
            return
        self._purge_expired()
        while len(self._entries) >= self._capacity:
            self._entries.popitem(last=False)
        self._entries[key] = (value, expires_at)

    def discard(self, key: Hashable) -> bool:
        """Remove ``key`` if present. Return whether anything was removed."""
        return self._entries.pop(key, None) is not None

    def _is_expired(self, entry: tuple[Any, float | None]) -> bool:
        expires_at = entry[1]
        return expires_at is not None and self._clock() >= expires_at

    def _purge_expired(self) -> None:
        for key in [key for key, entry in self._entries.items() if self._is_expired(entry)]:
            del self._entries[key]
