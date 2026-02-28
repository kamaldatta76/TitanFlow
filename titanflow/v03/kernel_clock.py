"""KernelClock: centralized monotonic time source."""

from __future__ import annotations

import time


class KernelClock:
    """Centralized monotonic clock for deterministic scheduling and testing."""

    @staticmethod
    def now() -> float:
        return time.monotonic()
