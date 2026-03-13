from __future__ import annotations

import asyncio
import json
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from titanocta.agent import TitanOctaAgentRuntime
from titanocta.backup import BackupManager
from titanocta.installer import TitanOctaInstaller
from titanocta.local_governance import bootstrap_local_governance
from titanocta.management import management_payload
from titanocta.remote_token import RemoteAttachTokenManager
from titanocta.routing import TitanOctaRouter
from titanocta.tai import TAi
from titanocta.tier_guard import TierGuard


def test_tier_guard_blocks_free_overages() -> None:
    # Free tier = 2 agents (Octa pair doctrine, locked 2026-03-13)
    guard = TierGuard()
    ok_one   = guard.enforce(users=1, agents=1, nodes=1)
    ok_two   = guard.enforce(users=1, agents=2, nodes=1)
    blocked  = guard.enforce(users=1, agents=3, nodes=1)
    assert ok_one.allowed is True
    assert ok_two.allowed is True   # 2 agents is the free tier ceiling
    assert blocked.allowed is False
    assert blocked.reason == "agents_limit_exceeded"


def test_remote_attach_token_roundtrip() -> None:
    manager = RemoteAttachTokenManager("test-secret", audit_log_path="/tmp/titanocta-token-audit.jsonl")
    token = manager.generate_token(subject="Titan", node_id="node-1", ttl_s=60)
    payload = manager.validate_token(token.token)
    assert payload["subject"] == "Titan"
    assert payload["node_id"] == "node-1"


def test_router_uses_governance_classifier() -> None:
    router = TitanOctaRouter(audit_log_path="/tmp/titanocta-routing-audit.jsonl")
    greeting = router.route("Atlas are you here?")
    mixed = router.route("Fix the ATLAS chatbox CSS and deploy the update on Mercury.")
    scout = router.route("Do a quick preflight summary and recon on this queue.")
    assert greeting.primary_agent == "charlie"
    assert greeting.execution_targets == ()
    assert mixed.execution_targets == ("ollie", "flow")
    assert scout.execution_targets == ("mini",)
    assert scout.classification == "scout_prep"


def test_router_enforces_golden_role_for_cc_chex_tasks() -> None:
    router = TitanOctaRouter(audit_log_path="/tmp/titanocta-routing-audit.jsonl")
    cc_route = router.route("@CC take this deployment lane.")
    chex_route = router.route("owner: Chex. Run this through the factory.")

    for route in (cc_route, chex_route):
        assert route.classification == "golden_role_factory"
        assert route.primary_agent == "charlie"
        assert route.execution_targets == ("ollie", "flow")
        assert route.notify_agents == ("cc", "cx")
        assert route.requires_executor_touch is True
        assert route.close_guard_targets == ("ollie", "flow")
        assert route.close_guard_policy == "all"
        assert route.required_subagent_lanes == ("dash", "octa", "flow")
        assert route.sweep_passes_required == 2


def test_backup_manifest_and_verify(tmp_path: Path) -> None:
    source = tmp_path / "config.json"
    source.write_text(json.dumps({"tier": "free"}), encoding="utf-8")
    manager = BackupManager(base_dir=str(tmp_path / "backups"))
    archive = manager.create_backup(source_paths=[str(source)], label="test")
    verify = manager.verify_backup(archive)
    assert verify["ok"] is True
    with tarfile.open(archive, "r:gz") as tf:
        assert "manifest.json" in tf.getnames()


def test_installer_bootstraps_local_governance(tmp_path: Path) -> None:
    # Pre-consent the install root so the gate doesn't block or prompt in tests.
    # This does NOT bypass the gate in production — it simulates a user who has
    # already agreed on a previous run. The gate itself is tested separately.
    from titanocta.telemetry_consent import TelemetryConsentGate
    install_root = tmp_path / "octa"
    install_root.mkdir(parents=True, exist_ok=True)
    TelemetryConsentGate(install_root).record_consent("test-node-001")

    with (
        patch("titanocta.installer.start_management_server_detached", return_value=False),
        patch.object(TitanOctaInstaller, "reconcile_constellation_registration", AsyncMock(return_value="registered")),
    ):
        result = asyncio.run(TitanOctaInstaller(install_root=str(install_root)).run())
    assert result.registration_id.startswith("titan@")
    assert result.health.flow == "green"
    assert result.octa_key.startswith("octa_")
    assert result.routing_config["provider_mode"] == "western_only"
    db_path = tmp_path / "octa" / "octopus-local.db"
    conn = sqlite3.connect(db_path)
    agents = conn.execute("select id, status from agents").fetchall()
    events = conn.execute("select event_type from events order by id desc limit 5").fetchall()
    conn.close()
    assert agents == [("titan", "online")]
    assert ("titanocta_agent_registered",) in events
    prov_db = sqlite3.connect(tmp_path / "octa" / "provisioning.sqlite")
    prov = prov_db.execute("select octa_key, tier, status from octa_users").fetchall()
    prov_db.close()
    assert len(prov) == 1
    assert prov[0][1] == "free"
    assert prov[0][2] == "active"


def test_remote_attach_token_expiry() -> None:
    manager = RemoteAttachTokenManager("test-secret", audit_log_path="/tmp/titanocta-token-audit.jsonl")
    token = manager.generate_token(subject="Titan", node_id="node-1", ttl_s=-1)
    try:
        manager.validate_token(token.token)
    except ValueError as exc:
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("Expected expired token to raise ValueError")


async def test_submit_user_message_full_governance_path(tmp_path: Path) -> None:
    """
    Gate 3 — Integration test: submit_user_message end-to-end.

    - Real SQLite DB (no mocking)
    - Real GovernanceEngine + LocalOctopusBus
    - Real TitanOctaRouter + TierGuard
    - Ollama client mocked — testing the governance path, not the model
    - Verifies: response text returned, decision created in DB, user memory persisted
    """
    # ── Bootstrap real governance ──────────────────────────────────────────────
    gov = await bootstrap_local_governance(str(tmp_path / "octa"))

    # ── Mock Ollama response ───────────────────────────────────────────────────
    _CANNED = "Titan here. The answer is 42."
    mock_client = MagicMock()
    mock_client.chat = AsyncMock(
        return_value=SimpleNamespace(message=SimpleNamespace(content=_CANNED))
    )

    # ── Build runtime with real governance stack ───────────────────────────────
    runtime = TitanOctaAgentRuntime(
        governance=gov.governance,
        bus=gov.bus,
        db=gov.db,
        agent_id="titan",
        node_id="local-test",
        tier_guard=TierGuard(),
        router=TitanOctaRouter(audit_log_path=str(tmp_path / "routing-audit.jsonl")),
        model="qwen2.5:7b",
        ollama_host="http://localhost:11434",
    )

    # ── Exercise the full governance path ─────────────────────────────────────
    intent = "What is the meaning of life?"
    with patch("ollama.AsyncClient", return_value=mock_client):
        response = await runtime.submit_user_message(intent, actor="user")

    # ── Assert: response is actual model text, not a decision ID ──────────────
    assert response == _CANNED, f"Expected canned model response, got: {response!r}"

    # ── Assert: task/decision created in DB ───────────────────────────────────
    conn = sqlite3.connect(gov.db_path)
    tasks = conn.execute("select intent from tasks").fetchall()
    events = conn.execute("select event_type from events").fetchall()
    mem_keys = [
        row[0] for row in conn.execute(
            "select key from memory where scope = 'governance'"
        ).fetchall()
    ]
    conn.close()

    task_intents = [row[0] for row in tasks]
    assert any(intent in t for t in task_intents), (
        f"Expected task with intent in DB, got: {task_intents}"
    )

    # ── Assert: governance events persisted ───────────────────────────────────
    event_types = {row[0] for row in events}
    assert event_types, "No events written to DB"

    # ── Assert: user memory persisted ─────────────────────────────────────────
    assert any("titanocta:user:user" in k for k in mem_keys), (
        f"User memory key not found in DB, keys: {mem_keys}"
    )


async def test_registration_pending_then_registered(tmp_path: Path) -> None:
    installer = TitanOctaInstaller(install_root=str(tmp_path / "octa"))
    config = {
        "node_id": "node-123",
        "tier": "free",
        "profile": {"class_name": "Lite"},
        "active_model": "qwen2.5:7b",
    }

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):  # noqa: A003
            raise RuntimeError("offline")

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

    class _SuccessClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):  # noqa: A003
            return _Response()

    with patch("titanocta.installer.httpx.AsyncClient", return_value=_FailingClient()):
        status = await installer.reconcile_constellation_registration(config)
    assert status == "pending"
    assert (tmp_path / "octa" / "pending-registration.json").exists()

    with patch("titanocta.installer.httpx.AsyncClient", return_value=_SuccessClient()):
        status = await installer.reconcile_constellation_registration(config)
    assert status == "registered"
    assert not (tmp_path / "octa" / "pending-registration.json").exists()


def test_tai_manual_check_and_ignore(tmp_path: Path) -> None:
    tai = TAi(install_root=str(tmp_path / "octa"), current_model="qwen2.5:7b")
    tai.record_signal("latency_spike")
    state = tai.update_after_response("short", "reasoning")
    assert state.last_score < 0.55
    suggestion = tai.suggest()
    assert suggestion is not None
    ignored = tai.ignore(cooldown_s=60)
    assert ignored.current_suggestion is None
    assert ignored.cooldown_until > 0


def test_management_payload_reads_installed_state(tmp_path: Path) -> None:
    root = tmp_path / "octa"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(
        json.dumps(
            {
                "tier": "free",
                "active_model": "qwen2.5:7b",
                "node_id": "node-abc",
                "health": {"flow": "green"},
            }
        ),
        encoding="utf-8",
    )
    payload = management_payload(str(root))
    assert payload["status"] == "ok"
    assert payload["flow_status"] == "green"
    assert payload["active_model"] == "qwen2.5:7b"
    assert payload["node_id"] == "node-abc"
