from __future__ import annotations

from typing import Iterable, List, Sequence


def chunked(seq: Sequence, size: int) -> List[Sequence]:
    size = max(1, size)
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class AdaptiveLimiter:
    """
    Simple adaptive worker limiter.
    - starts at start_workers
    - on success (zero errors) increments by 1 up to max_workers
    - on error (>0 errors) halves (floored) down to min 1
    """

    def __init__(self, start_workers: int, max_workers: int):
        self.current = max(1, start_workers)
        self.max_workers = max(1, max_workers)

    def record_batch(self, errors: int) -> int:
        if errors == 0 and self.current < self.max_workers:
            self.current = min(self.max_workers, self.current + 1)
        elif errors > 0 and self.current > 1:
            self.current = max(1, self.current // 2)
        return self.current

    def value(self) -> int:
        return self.current
