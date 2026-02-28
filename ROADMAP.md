# TitanFlow Roadmap — TitanSafe Ecosystem

This roadmap converts the OpenClaw audit into a phased TitanFlow delivery plan. Each phase includes module owners, acceptance tests, and pipeline tasks.

## Phase 1 — SSH Tools + Context Compression + Telemetry
**Goal:** safer ops, lower latency, and clear observability.

**Modules**
- **SSH Tool Gateway** (Owner: Flow)
  - Scoped allowlists, command templates, audit logging.
- **Context Compression Middleware** (Owner: CX)
  - Token reduction, context ranking, cache hits.
- **Telemetry Surface** (Owner: CC)
  - Export queue depth, DLQ size, cache stats, and watchdog lag.

**Acceptance Tests**
- SSH command rejected if not in allowlist.
- Context compression reduces prompt size without removing required fields.
- `/metrics` exposes queue depth + DLQ size + cache stats.

**Pipeline Tasks**
- Implement SSH module with policy layer.
- Add compression hooks in LLM broker.
- Add telemetry endpoint + dashboard widgets.

## Phase 2 — MQTT + HA Integration + Memory Upgrades
**Goal:** event‑driven automation + durable memory.

**Modules**
- **MQTT Bridge** (Owner: Flow)
  - Inbound triggers, outbound publish.
- **Home Assistant Adapter** (Owner: CX)
  - Device control, state queries.
- **Memory Provider Interface** (Owner: CC)
  - Core memory, LanceDB backend, upgrade path.

**Acceptance Tests**
- MQTT inbound message creates task with trace_id.
- HA device state query returns valid JSON.
- Memory provider can store + recall facts across restarts.

**Pipeline Tasks**
- Build MQTT module + tests.
- Build HA adapter (read + action).
- Implement memory provider selection.

## Phase 3 — Multi‑Channel + Sub‑Agent Coordination
**Goal:** unified comms + parallel research.

**Modules**
- **Multi‑Channel Gateway** (Owner: Flow)
  - Matrix, Nostr, Teams, Zalo, WeCom, Lark (prioritized).
- **Sub‑Agent Scheduler** (Owner: CX)
  - Parallel tasks, synthesis stage.

**Acceptance Tests**
- Message from any channel routes to the same core envelope.
- Sub‑agent task produces a synthesis response within SLA.

**Pipeline Tasks**
- Add channel adapters with shared schema.
- Add sub‑agent queue + summarizer.

## Phase 4 — Autonomous Coding + Marketplace
**Goal:** ship the TitanSafe marketplace with verified modules.

**Modules**
- **Autonomous Coding Loop** (Owner: Flow)
  - Generate → test → review → deploy.
- **Marketplace Verification** (Owner: CC)
  - Static checks, integration tests, signature validation.
- **Marketplace Web** (Owner: CX)
  - titanarray.net integration, plugin directory UI.

**Acceptance Tests**
- Pipeline rejects unverified modules.
- Marketplace lists verified modules with install docs.
- Autonomous coding loop produces a reviewed PR + deploy.

**Pipeline Tasks**
- Build verification harness + CI gate.
- Publish marketplace metadata schema.
- Automate release updates + blog logs.
