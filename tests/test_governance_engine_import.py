from __future__ import annotations

import governance_engine


def test_canonical_governance_engine_import_exports_runtime_symbols() -> None:
    assert hasattr(governance_engine, "GovernanceEngine")
    assert hasattr(governance_engine, "_build_dispatch_plan")
    assert callable(governance_engine.dispatch_close_guard_satisfied)

