"""Canonical governance_engine module.

This shim keeps runtime imports stable (`from governance_engine import ...`)
while the implementation currently lives in `octopus-api.governance_engine.py`.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any


_IMPL_PATH = Path(__file__).with_name("octopus-api.governance_engine.py")
_SPEC = spec_from_file_location("octopus_api_governance_engine", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load governance implementation from {_IMPL_PATH}")
_IMPL: ModuleType = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_IMPL)


def _export(name: str, value: Any) -> None:
    globals()[name] = value


for _name in dir(_IMPL):
    if _name.startswith("_") and _name not in {
        "_build_dispatch_plan",
        "_build_dispatch_prompt",
        "_split_dispatch_specs",
    }:
        continue
    _export(_name, getattr(_IMPL, _name))


__all__ = [name for name in globals() if not name.startswith("__")]

