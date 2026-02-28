"""v0.3 configuration loader (environment-driven)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CoreConfig:
    # Database broker
    db_max_queue: int = 500
    db_job_enqueue_timeout_s: float = 5.0
    db_job_exec_timeout_s: float = 5.0

    # WAL management
    wal_pressure_bytes: int = 268_435_456
    wal_passive_every_s: float = 900.0
    wal_truncate_every_s: float = 21_600.0

    # IPC queues
    ipc_out_q_max: int = 200
    ipc_in_q_max: int = 200

    # Shutdown
    shutdown_deadline_s: float = 5.0

    # Watchdog
    watchdog_sec: float = 20.0
    watchdog_lag_max_s: float = 0.5

    # Cache
    cache_max_bytes: int = 262_144
    cache_max_rows: int = 5_000
    cache_ttl_days: int = 7

    # Sessions / actors
    allowed_actors: tuple[str, ...] = ("kamal", "kellen", "ollie", "flow")
    session_ttl_days: int = 90

    # Telemetry
    telemetry_socket: str = "/run/titanflow/telemetry.sock"
    core_socket: str = "/run/titanflow/core.sock"


def _get_env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def load_config() -> CoreConfig:
    # Backward-compat: TITANFLOW_DB_JOB_TIMEOUT_S
    legacy_timeout = _get_float("TITANFLOW_DB_JOB_TIMEOUT_S", 5.0)

    return CoreConfig(
        db_max_queue=_get_int("TITANFLOW_DB_MAX_QUEUE", 500),
        db_job_enqueue_timeout_s=_get_float(
            "TITANFLOW_DB_JOB_ENQUEUE_TIMEOUT_S", legacy_timeout
        ),
        db_job_exec_timeout_s=_get_float(
            "TITANFLOW_DB_JOB_EXEC_TIMEOUT_S", legacy_timeout
        ),
        wal_pressure_bytes=_get_int("TITANFLOW_WAL_PRESSURE_BYTES", 268_435_456),
        wal_passive_every_s=_get_float("TITANFLOW_WAL_PASSIVE_EVERY_S", 900.0),
        wal_truncate_every_s=_get_float("TITANFLOW_WAL_TRUNCATE_EVERY_S", 21_600.0),
        ipc_out_q_max=_get_int("TITANFLOW_IPC_OUT_Q_MAX", 200),
        ipc_in_q_max=_get_int("TITANFLOW_IPC_IN_Q_MAX", 200),
        shutdown_deadline_s=_get_float("TITANFLOW_SHUTDOWN_DEADLINE_S", 5.0),
        watchdog_sec=_get_float("TITANFLOW_WATCHDOG_SEC", 20.0),
        watchdog_lag_max_s=_get_float("TITANFLOW_WATCHDOG_LAG_MAX_S", 0.5),
        cache_max_bytes=_get_int("TITANFLOW_CACHE_MAX_BYTES", 262_144),
        cache_max_rows=_get_int("TITANFLOW_CACHE_MAX_ROWS", 5_000),
        cache_ttl_days=_get_int("TITANFLOW_CACHE_TTL_DAYS", 7),
        allowed_actors=_get_list(
            "TITANFLOW_ALLOWED_ACTORS", ("kamal", "kellen", "ollie", "flow")
        ),
        session_ttl_days=_get_int("TITANFLOW_SESSION_TTL_DAYS", 90),
        telemetry_socket=_get_env(
            "TITANFLOW_TELEMETRY_SOCKET", "/run/titanflow/telemetry.sock"
        ),
        core_socket=_get_env(
            "TITANFLOW_CORE_SOCKET", "/run/titanflow/core.sock"
        ),
    )
