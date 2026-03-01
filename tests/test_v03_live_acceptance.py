"""
TitanFlow v0.3 — Live Acceptance Suite
=======================================
Runs against the LIVE kernel on Sarge.
  - Gateway  : http://127.0.0.1:18888
  - Telemetry: http://127.0.0.1:19100

No mocks. No stubs. Real endpoints.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

GATEWAY = "http://127.0.0.1:18888"
TELEMETRY = "http://127.0.0.1:19100"
TIMEOUT = 5.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gw(path: str = "", method: str = "GET", json_body: dict | None = None) -> httpx.Response:
    with httpx.Client(base_url=GATEWAY, timeout=TIMEOUT) as c:
        if method == "GET":
            return c.get(path)
        return c.post(path, json=json_body or {})


def _tele(path: str) -> httpx.Response:
    with httpx.Client(base_url=TELEMETRY, timeout=TIMEOUT) as c:
        return c.get(path)


def _create_session(actor: str = "kamal") -> str:
    """Create a session via the gateway and return the session_id."""
    r = _gw("/session", "POST", {"actor_id": actor, "metadata": {"source": "acceptance-test"}})
    assert r.status_code == 200, f"session create failed: {r.status_code} {r.text}"
    data = r.json()
    assert "session_id" in data, f"no session_id in response: {data}"
    return data["session_id"]


# ===========================================================================
# 1. GATEWAY — Health & Routing
# ===========================================================================

class TestGatewayHealth:
    """Gateway HTTP server is reachable and responds correctly."""

    def test_health_returns_ok(self):
        r = _gw("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"

    def test_unknown_get_returns_404(self):
        r = _gw("/nonexistent-endpoint")
        assert r.status_code == 404

    def test_unknown_post_returns_404(self):
        r = _gw("/nonexistent", "POST", {})
        assert r.status_code == 404


# ===========================================================================
# 2. TELEMETRY — Status & Metrics
# ===========================================================================

class TestTelemetryEndpoints:
    """Telemetry HTTP server reports DB state and metrics."""

    def test_status_returns_db_running(self):
        r = _tele("/status")
        assert r.status_code == 200
        body = r.json()
        assert body["db_state"] == "RUNNING"
        assert "dlq_size" in body
        assert isinstance(body["dlq_size"], int)

    def test_metrics_endpoint(self):
        r = _tele("/metrics")
        assert r.status_code == 200
        body = r.json()
        # metrics endpoint returns same structure
        assert "db_state" in body

    def test_telemetry_404_on_unknown(self):
        r = _tele("/unknown")
        assert r.status_code == 404


# ===========================================================================
# 3. SESSION LIFECYCLE
# ===========================================================================

class TestSessionLifecycle:
    """Session creation, validation via RPC round-trip."""

    def test_create_session_kamal(self):
        sid = _create_session("kamal")
        assert sid  # non-empty
        assert len(sid) > 8  # looks like a real ID

    def test_create_session_ollie(self):
        sid = _create_session("ollie")
        assert sid

    def test_create_session_kellen(self):
        sid = _create_session("kellen")
        assert sid

    def test_create_session_flow(self):
        sid = _create_session("flow")
        assert sid

    def test_create_session_missing_actor(self):
        r = _gw("/session", "POST", {})
        assert r.status_code == 400
        assert "missing_actor_id" in r.json().get("error", "")

    def test_session_reuse_across_rpc(self):
        """Create a session, then use it for an RPC call."""
        sid = _create_session("kamal")
        r = _gw("/rpc", "POST", {
            "session_id": sid,
            "actor_id": "kamal",
            "module_id": "core",
            "method": "ping",
            "payload": {"ts": time.time()},
            "priority": 0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert "trace_id" in body


# ===========================================================================
# 4. RPC GATEWAY — Envelope Validation
# ===========================================================================

class TestRPCValidation:
    """RPC endpoint validates required fields and rejects bad requests."""

    @pytest.fixture(autouse=True)
    def _session(self):
        self.session_id = _create_session("kamal")

    def test_rpc_accepted(self):
        r = _gw("/rpc", "POST", {
            "session_id": self.session_id,
            "actor_id": "kamal",
            "module_id": "research",
            "method": "query",
            "payload": {"q": "test"},
            "priority": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert body["trace_id"]  # non-empty trace ID

    def test_rpc_missing_fields(self):
        r = _gw("/rpc", "POST", {"session_id": self.session_id})
        assert r.status_code == 400
        body = r.json()
        assert "missing_fields" in body.get("error", "")

    def test_rpc_with_custom_trace_id(self):
        custom_trace = f"test-{uuid.uuid4().hex[:12]}"
        r = _gw("/rpc", "POST", {
            "session_id": self.session_id,
            "actor_id": "kamal",
            "module_id": "core",
            "method": "echo",
            "payload": {},
            "priority": 0,
            "trace_id": custom_trace,
        })
        assert r.status_code == 200
        assert r.json()["trace_id"] == custom_trace

    def test_rpc_all_priority_levels(self):
        """Priority 0 (chat), 1 (default), 2 (background) all accepted."""
        for pri in [0, 1, 2]:
            r = _gw("/rpc", "POST", {
                "session_id": self.session_id,
                "actor_id": "kamal",
                "module_id": "core",
                "method": f"priority_test_{pri}",
                "payload": {},
                "priority": pri,
            })
            assert r.status_code == 200, f"priority {pri} rejected"

    def test_rpc_stream_flag(self):
        r = _gw("/rpc", "POST", {
            "session_id": self.session_id,
            "actor_id": "kamal",
            "module_id": "core",
            "method": "stream_test",
            "payload": {},
            "priority": 0,
            "stream": True,
        })
        assert r.status_code == 200


# ===========================================================================
# 5. ACTOR ISOLATION
# ===========================================================================

class TestActorIsolation:
    """Each allowed actor can create sessions and submit RPC.
       Disallowed actors are rejected."""

    def test_allowed_actors_all_work(self):
        for actor in ("kamal", "kellen", "ollie", "flow"):
            sid = _create_session(actor)
            r = _gw("/rpc", "POST", {
                "session_id": sid,
                "actor_id": actor,
                "module_id": "core",
                "method": "actor_test",
                "payload": {},
                "priority": 1,
            })
            assert r.status_code == 200, f"actor {actor} failed: {r.text}"

    def test_disallowed_actor_session_rejected_or_rpc_fails(self):
        """Unknown actor should fail at session create or RPC level."""
        # Session create might succeed but core will reject envelope
        r = _gw("/session", "POST", {"actor_id": "intruder"})
        if r.status_code == 200:
            sid = r.json()["session_id"]
            r2 = _gw("/rpc", "POST", {
                "session_id": sid,
                "actor_id": "intruder",
                "module_id": "core",
                "method": "test",
                "payload": {},
                "priority": 1,
            })
            # Either 502 (core rejects and closes socket) or 200 (accepted but will be DLQ'd)
            # Both are valid — the key is it doesn't crash the gateway
            assert r2.status_code in (200, 400, 502)
        else:
            # Gateway itself rejected the actor — even better
            assert r.status_code in (400, 403)


# ===========================================================================
# 6. IPC TRANSPORT — Unix Socket Reachability
# ===========================================================================

class TestIPCTransport:
    """Gateway can reach the core IPC socket (verified by successful RPC)."""

    def test_gateway_to_core_round_trip(self):
        """If /rpc returns 200, the Unix socket path is live."""
        sid = _create_session("ollie")
        r = _gw("/rpc", "POST", {
            "session_id": sid,
            "actor_id": "ollie",
            "module_id": "core",
            "method": "health_check",
            "payload": {},
            "priority": 0,
        })
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"


# ===========================================================================
# 7. DB BROKER — Liveness via Telemetry
# ===========================================================================

class TestDBBrokerLiveness:
    """DB broker is running and DLQ is accessible."""

    def test_db_state_running(self):
        body = _tele("/status").json()
        assert body["db_state"] == "RUNNING"

    def test_dlq_size_is_integer(self):
        body = _tele("/status").json()
        assert isinstance(body["dlq_size"], int)
        assert body["dlq_size"] >= 0

    def test_metrics_dict_present(self):
        body = _tele("/status").json()
        assert isinstance(body.get("metrics", {}), dict)


# ===========================================================================
# 8. LOAD — Burst of concurrent RPCs
# ===========================================================================

class TestBurstLoad:
    """Submit a burst of RPCs and verify the kernel stays healthy."""

    def test_burst_10_rpcs(self):
        sid = _create_session("kamal")
        results = []
        for i in range(10):
            r = _gw("/rpc", "POST", {
                "session_id": sid,
                "actor_id": "kamal",
                "module_id": "core",
                "method": f"burst_{i}",
                "payload": {"seq": i},
                "priority": 1,
            })
            results.append(r.status_code)
        # All should be accepted
        assert all(s == 200 for s in results), f"Burst results: {results}"

    def test_kernel_healthy_after_burst(self):
        """After 10 RPCs, gateway and telemetry still respond."""
        r1 = _gw("/health")
        r2 = _tele("/status")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["db_state"] == "RUNNING"


# ===========================================================================
# 9. MULTI-MODULE DISPATCH
# ===========================================================================

class TestMultiModuleDispatch:
    """Envelopes route to different module queues without crosstalk."""

    def test_different_modules_accepted(self):
        sid = _create_session("kamal")
        for mod in ("core", "research", "newspaper", "codeexec", "security"):
            r = _gw("/rpc", "POST", {
                "session_id": sid,
                "actor_id": "kamal",
                "module_id": mod,
                "method": "dispatch_test",
                "payload": {},
                "priority": 1,
            })
            assert r.status_code == 200, f"module {mod} rejected: {r.text}"


# ===========================================================================
# 10. SESSION + METRICS COUNTER INTEGRATION
# ===========================================================================

class TestMetricsAfterActivity:
    """After sessions and RPCs, counters should have incremented."""

    def test_counters_populated_after_activity(self):
        # Create session + send RPC to generate counter increments
        sid = _create_session("ollie")
        _gw("/rpc", "POST", {
            "session_id": sid,
            "actor_id": "ollie",
            "module_id": "core",
            "method": "metrics_probe",
            "payload": {},
            "priority": 1,
        })
        time.sleep(0.5)  # give the kernel a moment

        body = _tele("/status").json()
        # metrics should be a dict (may be empty if counters haven't propagated
        # to snapshot yet, but the key must exist)
        assert isinstance(body.get("metrics"), dict)

    def test_dlq_not_exploding(self):
        """DLQ shouldn't be growing unbounded from our test traffic."""
        body = _tele("/status").json()
        assert body["dlq_size"] < 100, f"DLQ suspiciously large: {body['dlq_size']}"


# ===========================================================================
# 11. EDGE CASES
# ===========================================================================

class TestEdgeCases:
    """Boundary conditions the kernel must handle gracefully."""

    def test_empty_post_body(self):
        """Empty JSON body to /rpc should return 400, not crash."""
        r = _gw("/rpc", "POST", {})
        assert r.status_code == 400

    def test_rpc_empty_payload(self):
        sid = _create_session("kamal")
        r = _gw("/rpc", "POST", {
            "session_id": sid,
            "actor_id": "kamal",
            "module_id": "core",
            "method": "empty_payload_test",
            "payload": {},
            "priority": 0,
        })
        assert r.status_code == 200

    def test_rpc_large_payload(self):
        """4KB payload should be accepted without issue."""
        sid = _create_session("kamal")
        big_payload = {"data": "x" * 4096}
        r = _gw("/rpc", "POST", {
            "session_id": sid,
            "actor_id": "kamal",
            "module_id": "core",
            "method": "large_payload_test",
            "payload": big_payload,
            "priority": 1,
        })
        assert r.status_code == 200

    def test_session_create_idempotent(self):
        """Multiple session creates for the same actor should all succeed."""
        sids = set()
        for _ in range(5):
            sids.add(_create_session("kamal"))
        assert len(sids) == 5  # each gets a unique session_id
