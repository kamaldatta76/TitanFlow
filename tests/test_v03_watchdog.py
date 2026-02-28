"""Watchdog lag gate tests (scaffold)."""

from __future__ import annotations

import asyncio

import pytest

from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.watchdog import Watchdog


class FakeClock(KernelClock):
    def __init__(self):
        self._now = 0.0

    def advance(self, seconds: float) -> None:
        self._now += seconds

    @staticmethod
    def now() -> float:  # type: ignore[override]
        return FakeClock._now  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_watchdog_lag_gate(monkeypatch):
    clock = FakeClock()
    sent = []

    def fake_notify(payload: str):
        sent.append(payload)

    monkeypatch.setattr("titanflow.v03.watchdog._sd_notify", fake_notify)
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/fake")

    async def healthy():
        return True

    wd = Watchdog(clock=clock, watchdog_sec=2, lag_max_s=0.5, health_check=healthy)
    await wd.start()

    # advance time with minimal lag
    clock.advance(1.0)
    await asyncio.sleep(1.1)

    # introduce artificial lag
    clock.advance(2.0)
    await asyncio.sleep(1.1)

    await wd.stop()

    assert any("WATCHDOG" in item for item in sent)
