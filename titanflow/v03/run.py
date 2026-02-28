"""Entry point for v0.3 core scaffold."""

from __future__ import annotations

import asyncio
import logging
import os

from titanflow.v03.config import load_config
from titanflow.v03.core import Core

logging.basicConfig(level=logging.INFO)


def main() -> None:
    config = load_config()
    db_path = os.environ.get("TITANFLOW_DB_PATH", "/data/titanflow/titanflow.db")

    core = Core(config=config, db_path=db_path)

    async def _runner():
        await core.start()
        # Block forever
        await asyncio.Event().wait()

    asyncio.run(_runner())


if __name__ == "__main__":
    main()
