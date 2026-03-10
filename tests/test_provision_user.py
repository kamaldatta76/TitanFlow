from __future__ import annotations

import sqlite3
from pathlib import Path

from titanocta.provisioning import cancel_user, load_tier_config, provision_user


def test_load_tier_config_reads_single_source() -> None:
    config = load_tier_config()
    assert config["defaults"]["provider_mode"] == "western_only"
    assert config["defaults"]["excluded_models"] == ["minimax-m2.5", "qwen-flash"]
    assert config["tiers"]["pro"]["credit_limit"] == 15.0
    assert config["tiers"]["ultra"]["credit_limit"] == 40.0


def test_provision_user_returns_expected_defaults(tmp_path: Path) -> None:
    result = provision_user("user-1", "pro", "u@example.com", db_path=tmp_path / "provisioning.sqlite")
    assert result["tier"] == "pro"
    assert result["status"] == "active"
    assert result["routing_config"]["provider_mode"] == "western_only"
    assert result["routing_config"]["excluded_models"] == ["minimax-m2.5", "qwen-flash"]
    assert result["routing_config"]["warning_thresholds"] == [0.80, 0.95]
    assert result["routing_config"]["credit_limit_monthly"] == 15.0


def test_upgrade_keeps_octa_key_stable(tmp_path: Path) -> None:
    db_path = tmp_path / "provisioning.sqlite"
    first = provision_user("user-2", "pro", "u2@example.com", db_path=db_path)
    second = provision_user("user-2", "ultra", "u2@example.com", db_path=db_path)
    assert first["octa_key"] == second["octa_key"]
    assert second["tier"] == "ultra"
    assert second["routing_config"]["credit_limit_monthly"] == 40.0


def test_cancel_sets_cancelled_and_zeroes_credits(tmp_path: Path) -> None:
    db_path = tmp_path / "provisioning.sqlite"
    created = provision_user("user-3", "pro", "u3@example.com", db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("update octa_users set credit_used = 7.5 where user_id = ?", ("user-3",))
    conn.commit()
    conn.close()

    cancelled = cancel_user("user-3", db_path=db_path)
    assert cancelled["status"] == "cancelled"
    assert cancelled["octa_key"] == created["octa_key"]
    assert cancelled["routing_config"]["credit_used"] == 0.0

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "select status, credit_used from octa_users where user_id = ?",
        ("user-3",),
    ).fetchone()
    audit_types = [r[0] for r in conn.execute("select event_type from octa_audit order by id").fetchall()]
    conn.close()
    assert row == ("cancelled", 0.0)
    assert audit_types == ["key_provisioned", "user_cancelled", "key_revoked"]


def test_tier_change_audit_written(tmp_path: Path) -> None:
    db_path = tmp_path / "provisioning.sqlite"
    provision_user("user-4", "builder", "u4@example.com", db_path=db_path)
    provision_user("user-4", "pro", "u4@example.com", db_path=db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute("select event_type, metadata from octa_audit order by id").fetchall()
    conn.close()
    assert rows[0][0] == "key_provisioned"
    assert rows[1][0] == "tier_changed"
    assert '"old_tier": "builder"' in rows[1][1]
    assert '"new_tier": "pro"' in rows[1][1]
