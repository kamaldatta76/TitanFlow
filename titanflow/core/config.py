"""TitanFlow v0.2 Core configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CORE_CONFIG_PATH = "/etc/titanflow/titanflow-core.yaml"


def _resolve_env_vars(data: Any) -> Any:
    if isinstance(data, str) and data.startswith("${") and data.endswith("}"):
        return os.environ.get(data[2:-1], "")
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars(v) for v in data]
    return data


class CoreSettings(BaseModel):
    instance_name: str = "flow"
    socket_path: str = "/run/titanflow/core.sock"
    pid_file: str = "/run/titanflow/core.pid"


class TelegramSettings(BaseModel):
    bot_token: str = ""
    allowed_users: list[int] = Field(default_factory=list)
    typing_indicator: bool = True

    @field_validator("allowed_users", mode="before")
    @classmethod
    def _coerce_none_to_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            return [int(part) for part in raw.replace(";", ",").split(",") if part.strip()]
        return v


class LLMSettings(BaseModel):
    default_model: str = "flow:24b"
    fallback_model: str = "qwen3:14b"
    cloud_provider: str = "anthropic"
    cloud_api_key: str = ""
    cloud_model: str = "claude-sonnet-4-5-20250929"
    semaphore_limit: int = 1
    timeout_seconds: int = 120
    priority_levels: dict[str, int] = Field(default_factory=lambda: {
        "chat": 0,
        "module": 1,
        "research": 2,
    })


class DatabaseSettings(BaseModel):
    path: str = "/data/titanflow/titanflow.db"
    wal_mode: bool = True
    busy_timeout_ms: int = 5000
    max_rows_per_query: int = 1000


class AuditSettings(BaseModel):
    enabled: bool = True
    retention_days: int = 90


class ModulesSettings(BaseModel):
    manifest_dir: str = "/etc/titanflow/manifests"
    health_check_interval: int = 60
    restart_max_attempts: int = 3
    restart_backoff_seconds: int = 10


class HttpProxySettings(BaseModel):
    max_requests_per_minute: int = 60
    timeout_seconds: int = 30
    max_body_bytes: int = 50000


class CoreConfig(BaseModel):
    core: CoreSettings = CoreSettings()
    telegram: TelegramSettings = TelegramSettings()
    llm: LLMSettings = LLMSettings()
    database: DatabaseSettings = DatabaseSettings()
    audit: AuditSettings = AuditSettings()
    modules: ModulesSettings = ModulesSettings()
    http_proxy: HttpProxySettings = HttpProxySettings()


def load_core_config(path: str | Path | None = None) -> CoreConfig:
    if path is None:
        path = os.environ.get("TITANFLOW_CORE_CONFIG", DEFAULT_CORE_CONFIG_PATH)
    path = Path(path)
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        resolved = _resolve_env_vars(raw)
        return CoreConfig(**resolved)
    return CoreConfig()
