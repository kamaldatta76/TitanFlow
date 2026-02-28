"""Watchdog lag gate tests (scaffold)."""

from __future__ import annotations

import asyncio

import pytest

from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.watchdog import Watchdog


class FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def now(self) -> float:
        return self._now


@pytest.mark.asyncio
async def test_watchdog_sends_when_no_lag(monkeypatch):
    clock = FakeClock()
    sent = []

    def fake_notify(payload: str):
        sent.append(payload)

    monkeypatch.setattr("titanflow.v03.watchdog._sd_notify", fake_notify)
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/fake")

    async def healthy():
        return True

    original_sleep = asyncio.sleep
    steps = [1.0]

    async def fake_sleep(interval: float):
        if steps:
            clock.advance(steps.pop(0))
        await original_sleep(0)

    monkeypatch.setattr("titanflow.v03.watchdog.asyncio.sleep", fake_sleep)

    wd = Watchdog(clock=clock, watchdog_sec=2, lag_max_s=0.5, health_check=healthy)
    await wd.start()
    await original_sleep(0.01)
    await wd.stop()

    assert any("WATCHDOG" in item for item in sent)


@pytest.mark.asyncio
async def test_watchdog_skips_on_lag(monkeypatch):
    clock = FakeClock()
    sent = []

    def fake_notify(payload: str):
        sent.append(payload)

    monkeypatch.setattr("titanflow.v03.watchdog._sd_notify", fake_notify)
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/fake")

    async def healthy():
        return True

    original_sleep = asyncio.sleep
    steps = [2.0]

    async def fake_sleep(interval: float):
        if steps:
            clock.advance(steps.pop(0))
        await original_sleep(0)

    monkeypatch.setattr("titanflow.v03.watchdog.asyncio.sleep", fake_sleep)

    wd = Watchdog(clock=clock, watchdog_sec=2, lag_max_s=0.5, health_check=healthy)
    await wd.start()
    await original_sleep(0.01)
    await wd.stop()

    assert not any("WATCHDOG" in item for item in sent)
