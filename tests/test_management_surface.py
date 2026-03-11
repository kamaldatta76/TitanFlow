from __future__ import annotations

import json
from pathlib import Path

from titanocta.management import _sqlite_wal_safe, management_payload


def test_sqlite_wal_signal_logic() -> None:
    assert _sqlite_wal_safe("3.52.0") is True
    assert _sqlite_wal_safe("3.51.9") is False


def test_management_payload_includes_api_stub_and_sqlite_flags(tmp_path: Path) -> None:
    root = tmp_path / "octa"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(
        json.dumps(
            {
                "tier": "free",
                "active_model": "qwen2.5:7b",
                "node_id": "node-test",
                "health": {"flow": "green"},
            }
        ),
        encoding="utf-8",
    )
    payload = management_payload(str(root))
    assert payload["tier"] == "free"
    assert payload["active_model"] == "qwen2.5:7b"
    assert payload["flow_status"] == "green"
    assert "sqlite_version" in payload
    assert isinstance(payload["sqlite_wal_safe"], bool)
