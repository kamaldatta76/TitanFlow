"""
TitanOcta — Telemetry Consent Gate

DOCTRINE (Papa + CL, 2026-03-13):
  Mandatory on first run. No agreement = no access. No exceptions.

WHAT IS COLLECTED (the complete list — no drift allowed):
  - Hardware class (CPU tier, RAM, GPU presence/class — not serial numbers)
  - Local LLM model name and version in use
  - Benchmark / performance scores (tokens/sec, response latency)
  - Crash reports and deploy stats
  - Octa version + tier

WHAT IS NEVER COLLECTED (hard no — enforced here, not just policy):
  - Prompt content or session text
  - Session history or memory data
  - API keys or credentials
  - IP address or network topology
  - Personal names or account details beyond device_token UUID

WHY IT IS MANDATORY:
  This is what makes the community Grafana dashboard real.
  Real hardware, real performance, real benchmark pool.
  Without it the dashboard is theater. With it, it's infrastructure.

MARKETING NOTE (CL):
  Do not say "your data stays yours" anywhere in public comms after this.
  The accurate claim is: "Your conversations stay yours. Your hardware
  performance data feeds the community benchmark pool."
  That is honest. The other version is not.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONSENT_FILENAME = ".telemetry_consent"
CONSENT_VERSION  = "1.0"


# Exact fields collected — if you add a field, bump CONSENT_VERSION
COLLECTED_FIELDS = [
    "hardware_class",
    "hardware_ram_gb_bucket",   # bucketed (e.g. "8-16GB"), not exact
    "hardware_gpu_class",       # e.g. "amd_rdna3" / "nvidia_ada" / "cpu_only"
    "local_llm_model",
    "octa_version",
    "tier",
    "benchmark_tokens_per_sec",
    "benchmark_response_latency_ms",
    "crash_events",
    "deploy_stats",
]

NEVER_COLLECTED = [
    "prompt_content",
    "session_text",
    "session_history",
    "memory_data",
    "api_keys",
    "credentials",
    "ip_address",
    "network_topology",
    "personal_names",
]

CONSENT_TEXT = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TitanOcta — Community Performance Data

  To run Octa you agree to share hardware and performance
  data with the TitanOcta community benchmark pool.

  WHAT IS SHARED:
    · Hardware class (CPU tier, RAM bucket, GPU class)
    · Local LLM model name and benchmark scores
    · Tokens/sec, response latency, deploy stats
    · Crash reports
    · Octa version and tier

  WHAT IS NEVER SHARED:
    · Your prompts or conversation content
    · Your session history or agent memory
    · Your API keys or credentials
    · Your IP address

  This data powers the public Grafana dashboard at
  titanocta.com — real hardware, real performance, real pool.

  No agreement = no access. This is not optional.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


@dataclass(frozen=True)
class ConsentRecord:
    consented: bool
    consent_version: str
    consented_at_ms: int
    node_id: str
    collected_fields: list[str]
    never_collected: list[str]


class TelemetryConsentGate:
    """
    Blocks Octa startup until the user has explicitly agreed to the
    telemetry doctrine. Consent is stored in the install root.
    Once given it is not asked again unless the consent_version bumps.
    """

    def __init__(self, install_root: Path) -> None:
        self._install_root = install_root
        self._consent_path = install_root / CONSENT_FILENAME

    def is_consented(self) -> bool:
        """True if a valid, current-version consent record exists."""
        if not self._consent_path.exists():
            return False
        try:
            record = json.loads(self._consent_path.read_text())
            return (
                record.get("consented") is True
                and record.get("consent_version") == CONSENT_VERSION
            )
        except Exception:
            return False

    def record_consent(self, node_id: str) -> ConsentRecord:
        """Persist consent. Called after the user explicitly agrees."""
        record = ConsentRecord(
            consented=True,
            consent_version=CONSENT_VERSION,
            consented_at_ms=int(time.time() * 1000),
            node_id=node_id,
            collected_fields=COLLECTED_FIELDS,
            never_collected=NEVER_COLLECTED,
        )
        self._install_root.mkdir(parents=True, exist_ok=True)
        self._consent_path.write_text(json.dumps(asdict(record), indent=2))
        return record

    def require_consent(self, node_id: str, *, interactive: bool = True) -> None:
        """
        Enforce the consent gate. Raises ConsentRequired if not consented.
        In interactive mode (CLI), prints the consent text and prompts.
        In non-interactive mode (programmatic), raises immediately if not consented.
        """
        if self.is_consented():
            return

        if not interactive:
            raise ConsentRequired(
                "TitanOcta requires consent to community performance telemetry. "
                "Run the installer interactively to agree."
            )

        print(CONSENT_TEXT)
        response = input("Type 'agree' to continue, anything else to exit: ").strip().lower()
        if response != "agree":
            raise ConsentRequired("Consent not given. TitanOcta will not start.")

        self.record_consent(node_id)
        print("\n✓ Consent recorded. Starting TitanOcta.\n")

    def consent_summary(self) -> dict[str, Any] | None:
        """Return the stored consent record as a dict, or None if not consented."""
        if not self._consent_path.exists():
            return None
        try:
            return json.loads(self._consent_path.read_text())
        except Exception:
            return None


class ConsentRequired(RuntimeError):
    """Raised when Octa is started without telemetry consent."""
