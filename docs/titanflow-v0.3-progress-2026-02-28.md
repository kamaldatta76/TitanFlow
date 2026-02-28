# TitanFlow v0.3 Scaffold — Progress Log (2026-02-28)

## Summary
This log captures all work completed so far for the TitanFlow v0.3 microkernel scaffold, including Phase 1–4 files, later additions, and the Ghost posts published.

## Phase 1 — Kernel Spine (Completed)
**Files added**
- `titanflow/v03/kernel_clock.py`
  - Centralized monotonic `KernelClock.now()`
- `titanflow/v03/config.py`
  - Env-driven config with defaults
  - Splits DB enqueue vs exec timeout
  - Adds telemetry socket config
- `titanflow/v03/db_broker.py`
  - Single-writer SQLite thread
  - Backpressure via `queue.Queue`
  - WAL pressure checkpoint
  - DLQ insert + metrics counters helpers
  - Exec timeout handling → DLQ + metrics
- `titanflow/v03/watchdog.py`
  - systemd `sd_notify` (READY/WATCHDOG)
  - Lag gate and health gate

**Schema updates embedded**
- DLQ: `method`, `queue`, `age_ms` columns
- Cache: `system_prompt_version`
- Metrics: `metrics_counters` + helper

## Phase 2 — Transport Nervous System (Completed)
**Files added**
- `titanflow/v03/trace_id.py`
  - ULID preferred, UUID fallback
- `titanflow/v03/logging.py`
  - `LoggerAdapter` with trace fields
  - JSON formatter for structured logs
- `titanflow/v03/session_manager.py`
  - Session validation + cleanup
  - Actor isolation enforcement
- `titanflow/v03/ipc_server.py`
  - Bounded inbound/outbound queues
  - TTL drops with DLQ insertion
  - Token bucket rate limiter
  - Enforces trace_id/session/actor envelope

## Phase 3 — Intelligence Layer (Completed)
**Files added**
- `titanflow/v03/llm_broker.py`
  - Preemption with dedicated task
  - Max preemptions → DLQ
  - Cache get/put + eviction
  - Size cap enforced
- `titanflow/v03/cache_manager.py`
  - Eviction scheduler wrapper

## Phase 4 — Gateway & Userland (Completed)
**Files added**
- `titanflow/v03/gateway.py`
  - Session validation
  - Actor allowlist enforcement
  - Trace ID injection into IPC envelope
- `titanflow/v03/workspace_manager.py`
  - Actor-isolated workspace root enforcement
- `titanflow/v03/codeexec.py`
  - CodeExec scaffold scoped to actor workspace

## Telemetry + Core Wiring (Added)
**Files added**
- `titanflow/v03/telemetry.py`
  - DLQ size + metrics snapshot
- `titanflow/v03/telemetry_server.py`
  - AF_UNIX telemetry socket server
- `titanflow/v03/telemetry_http.py`
  - HTTP bridge scaffold (`/metrics`, `/status`)
- `titanflow/v03/scheduler.py`
  - Async scheduler for periodic jobs
- `titanflow/v03/core.py`
  - Core runner scaffold
  - Starts DB broker + telemetry server
  - Schedules WAL checkpoints
  - Starts watchdog with READY signal

## Tests / Validation
- `tests/test_v03_ipc.py` (TTL drop test scaffold)
- `python3 -m compileall titanflow/v03` executed successfully.

## Ghost Posts Published
1. **TitanFlow Kernel — Executive Brief**
   - URL: `https://titanarray.net/titanflow-kernel-executive-brief/`

2. **TitanFlow: The Kernel That Turned an AI Into an Operating System**
   - URL: `https://titanarray.net/titanflow-kernel-ai-into-os/`
   - Includes “Impressive Features” section

## Backups
- Pre-0.3 scaffold archive created:
  - `/Users/kamaldatta/Documents/TitanArray/Backups/TitanFlow/Charlie/2026-02-28/pre-0.3-scaffold/titanflow-repo-full-2026-02-28-pre-0.3-scaffold.tar.gz`
  - SHA256: `6dda0cbffba41ea409e519aa82446ea0241bd2aa0d20dc268464f59dd1e82e68`

## Next Steps (Per User Request)
- Wire cache eviction + session cleanup into scheduler
- Add tests for:
  - Watchdog lag skip
  - WAL pressure checkpoint trigger
  - DLQ inserts for TTL and max preemptions

## Updates After Acceptance Pass
**IPC routing updates**
- Added `next_inbound_any` / `next_outbound_any` for core-wide queues.
- `IPCInboundLoop` now supports consuming from all modules (module_id optional).
- `IPCOutboundLoop` now drains outbound queues (not inbound) with TTL enforcement.
- `ModuleDispatcher` now pulls from outbound queues and resolves module socket path.

**Core wiring**
- Core now starts inbound loop without module scoping.
- LLM broker is created when an `llm_stream_fn` is provided.
- `llm.request` routed to broker with async task + outbound response (`llm.response` / `llm.error`).
- `sessions.create` handled in core inbound with metrics increment.

**Gateway**
- Added `gateway_http.py` with `/session`, `/rpc`, `/health`.
- Added `gateway_runner.py` entrypoint.
- Added systemd unit: `deploy/systemd/titanflow-v03-gateway.service`.
- Updated `deploy/README-v03.md` to include gateway service steps.

**Run wiring**
- `titanflow/v03/run.py` now injects LLM stream using `LLMClient`.

**Tests**
- `tests/test_v03_acceptance.py` updated to exercise outbound queue path.

**Test run note**
- Attempted: `python3 -m pytest tests/test_v03_acceptance.py tests/test_v03_ipc.py tests/test_v03_llm.py tests/test_v03_wal.py tests/test_v03_watchdog.py`
- Result: `pytest` not installed in this environment.

## Deployment Fixes (Sarge)
**systemd v0.3 service adjustments**
- DB path moved to `/var/lib/titanflow/core/titanflow.db` to work with `DynamicUser` sandbox.
- `ReadWritePaths` updated to `/var/lib/titanflow/core /run/titanflow-core`.
- `TITANFLOW_TELEMETRY_SOCKET` + `TITANFLOW_CORE_SOCKET` set to `/run/titanflow-core/*.sock`.

**telemetry socket alignment**
- Telemetry socket unit now listens on `/run/titanflow-core/telemetry.sock`.
- Telemetry HTTP runner updated to query `/run/titanflow-core/telemetry.sock`.
- Telemetry server now creates parent dir and chmods socket `0666` for cross-service access.

**Sarge runtime fixes**
- Rebuilt `/opt/titanflow/.venv` on Sarge with Linux Python 3.14 + pip install.
- v0.3 core confirmed `active (running)` after service tweaks.

**Telemetry access fix**
- Core `RuntimeDirectoryMode` relaxed to `0755` so telemetry HTTP can connect to `/run/titanflow-core/telemetry.sock`.
