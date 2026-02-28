"""Simple smoke helpers for v0.3 scaffold."""

from __future__ import annotations

import asyncio

from titanflow.v03.config import load_config
from titanflow.v03.core import Core


async def run_smoke() -> None:
    config = load_config()
    core = Core(config=config, db_path="/tmp/titanflow-v03-smoke.db")
    await core.start()
    await asyncio.sleep(0.1)
    await core.stop()


if __name__ == "__main__":
    asyncio.run(run_smoke())
