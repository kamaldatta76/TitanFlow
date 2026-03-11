from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from titanocta.credits import CreditMiddleware
from titanocta.provisioning import provision_user


def test_credit_middleware_emits_threshold_and_soft_cap_events(tmp_path: Path) -> None:
    db_path = tmp_path / "provisioning.sqlite"
    provision_user("user-credits", "pro", "credits@example.com", db_path=db_path)
    middleware = CreditMiddleware(db_path)

    first = middleware.debit_credit("user-credits", 12.0)
    assert first.warning_events == ("credit_warning_80",)
    assert first.soft_cap_engaged is False
    assert first.credit_used == 12.0

    second = middleware.debit_credit("user-credits", 3.0)
    assert second.warning_events == ("credit_warning_95",)
    assert second.soft_cap_engaged is True
    assert second.credit_used == 15.0
    assert second.soft_cap_strategy == "cheapest_allowed"

    conn = sqlite3.connect(db_path)
    rows = conn.execute("select event_type from octa_audit where user_id = ? order by id", ("user-credits",)).fetchall()
    conn.close()
    assert [row[0] for row in rows] == [
        "key_provisioned",
        "credit_warning_80",
        "credit_warning_95",
        "soft_cap_engaged",
    ]


def test_credit_middleware_rejects_unknown_user(tmp_path: Path) -> None:
    middleware = CreditMiddleware(tmp_path / "provisioning.sqlite")
    with pytest.raises(ValueError, match="Unknown TitanOcta user"):
        middleware.debit_credit("missing-user", 1.0)
