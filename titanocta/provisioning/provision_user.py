"""TitanOcta tier provisioning."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

import yaml

DEFAULT_TIERS_PATH = Path(__file__).with_name("tiers.yaml")
DEFAULT_DB_PATH = Path(os.environ.get("TITANOCTA_PROVISIONING_DB", "~/.titanocta/provisioning.sqlite")).expanduser()


def load_tier_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    path = Path(config_path) if config_path else DEFAULT_TIERS_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "tiers" not in data or "defaults" not in data:
        raise ValueError("Invalid TitanOcta tier configuration")
    return data


def provision_user(
    user_id: str,
    tier: str,
    email: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    config = load_tier_config(config_path)
    defaults = dict(config["defaults"])
    tier_config = dict(config["tiers"][tier])
    now = _utc_now()
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = _connect(db)
    _ensure_schema(conn)

    existing = conn.execute(
        "select octa_key, credit_used, status, tier from octa_users where user_id = ?",
        (user_id,),
    ).fetchone()

    octa_key = existing[0] if existing else _generate_octa_key()
    credit_used = float(existing[1]) if existing else 0.0
    old_tier = existing[3] if existing else None

    routing_config = _build_routing_config(defaults, tier_config, credit_used=credit_used)
    status = str(defaults["status"])

    conn.execute(
        """
        insert into octa_users (
            user_id, email, octa_key, tier, status, credit_limit_monthly, credit_used,
            provider_mode, available_models, excluded_models, mode, auto_strategy,
            warning_thresholds, hard_cap, soft_cap_strategy, local_ollama_configured,
            provisioned_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(user_id) do update set
            email = excluded.email,
            tier = excluded.tier,
            status = excluded.status,
            credit_limit_monthly = excluded.credit_limit_monthly,
            provider_mode = excluded.provider_mode,
            available_models = excluded.available_models,
            excluded_models = excluded.excluded_models,
            mode = excluded.mode,
            auto_strategy = excluded.auto_strategy,
            warning_thresholds = excluded.warning_thresholds,
            hard_cap = excluded.hard_cap,
            soft_cap_strategy = excluded.soft_cap_strategy,
            local_ollama_configured = excluded.local_ollama_configured,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            email,
            octa_key,
            tier,
            status,
            float(tier_config["credit_limit"]),
            credit_used,
            routing_config["provider_mode"],
            json.dumps(routing_config["available_models"]),
            json.dumps(routing_config["excluded_models"]),
            routing_config["mode"],
            routing_config["auto_strategy"],
            json.dumps(routing_config["warning_thresholds"]),
            1 if routing_config["hard_cap"] else 0,
            routing_config["soft_cap_strategy"],
            1 if routing_config["local_ollama_configured"] else 0,
            now,
            now,
        ),
    )
    if existing is None:
        _append_audit(conn, user_id, "key_provisioned", {"tier": tier, "timestamp": now})
    elif old_tier != tier:
        _append_audit(conn, user_id, "tier_changed", {"old_tier": old_tier, "new_tier": tier})
    conn.commit()
    conn.close()
    return {
        "octa_key": octa_key,
        "tier": tier,
        "routing_config": routing_config,
        "provisioned_at": now,
        "status": status,
    }


def cancel_user(
    user_id: str,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = _connect(db)
    _ensure_schema(conn)
    row = conn.execute(
        """
        select octa_key, tier, available_models, excluded_models, mode, auto_strategy,
               credit_limit_monthly, warning_thresholds, hard_cap, soft_cap_strategy,
               provider_mode, local_ollama_configured
        from octa_users where user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown TitanOcta user: {user_id}")
    now = _utc_now()
    conn.execute(
        """
        update octa_users
        set status = ?, credit_used = 0.0, updated_at = ?
        where user_id = ?
        """,
        ("cancelled", now, user_id),
    )
    _append_audit(conn, user_id, "user_cancelled", {"cancellation_date": now})
    _append_audit(conn, user_id, "key_revoked", {"reason": "cancelled", "timestamp": now})
    conn.commit()
    conn.close()
    return {
        "octa_key": row[0],
        "tier": row[1],
        "routing_config": {
            "available_models": json.loads(row[2]),
            "excluded_models": json.loads(row[3]),
            "mode": row[4],
            "auto_strategy": row[5],
            "credit_limit_monthly": float(row[6]),
            "credit_used": 0.0,
            "soft_cap_strategy": row[9],
            "warning_thresholds": json.loads(row[7]),
            "hard_cap": bool(row[8]),
            "provider_mode": row[10],
            "local_ollama_configured": bool(row[11]),
        },
        "provisioned_at": now,
        "status": "cancelled",
    }


def _build_routing_config(
    defaults: dict[str, object],
    tier_config: dict[str, object],
    *,
    credit_used: float,
) -> dict[str, object]:
    return {
        "available_models": list(tier_config["available_models"]),
        "excluded_models": list(defaults["excluded_models"]),
        "mode": defaults["mode"],
        "auto_strategy": defaults["auto_strategy"],
        "credit_limit_monthly": float(tier_config["credit_limit"]),
        "credit_used": float(credit_used),
        "soft_cap_strategy": tier_config.get("soft_cap_strategy"),
        "warning_thresholds": list(defaults["warning_thresholds"]),
        "hard_cap": bool(defaults["hard_cap"]),
        "provider_mode": str(defaults["provider_mode"]),
        "local_ollama_configured": bool(defaults["local_ollama_configured"]),
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists octa_users (
            user_id text primary key,
            email text not null,
            octa_key text not null,
            tier text not null,
            status text not null,
            credit_limit_monthly real not null,
            credit_used real not null default 0.0,
            provider_mode text not null,
            available_models text not null,
            excluded_models text not null,
            mode text not null,
            auto_strategy text not null,
            warning_thresholds text not null,
            hard_cap integer not null,
            soft_cap_strategy text,
            local_ollama_configured integer not null,
            provisioned_at text not null,
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists octa_audit (
            id integer primary key autoincrement,
            user_id text not null,
            timestamp text not null,
            event_type text not null,
            metadata text not null
        )
        """
    )


def _append_audit(conn: sqlite3.Connection, user_id: str, event_type: str, metadata: dict[str, object]) -> None:
    conn.execute(
        "insert into octa_audit (user_id, timestamp, event_type, metadata) values (?, ?, ?, ?)",
        (user_id, _utc_now(), event_type, json.dumps(metadata, sort_keys=True)),
    )


def _generate_octa_key() -> str:
    return f"octa_{uuid.uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
