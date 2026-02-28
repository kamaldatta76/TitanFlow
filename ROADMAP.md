# TitanFlow Roadmap — Merged OpenClaw Audits (2026-02-28)

This roadmap merges CX + CC independent audits into a unified priority plan for TitanFlow and the TitanSafe marketplace.

## Merge Summary (Resolved)
**Agreed top priorities**
- Workflow pipelines with approval gates
- Plugin/skill registry + installer
- Shell/SSH execution with approval
- Multi‑agent routing / delegation

**Notes**
- Memory backends are already present (mem0 + vector DB); focus is on stability + policy.
- Voice/telephony is valuable but not a discharge blocker → moved to Phase 3.
- Browser automation and advanced channels remain optional until core pipeline is stable.

---

## Unified Priority Ranking (Effort + Notes)

| Rank | Feature | Why it matters | Est. Effort | Notes |
|------|---------|----------------|-------------|-------|
| 1 | Workflow pipelines (approval gates) | Pipeline v1.0 backbone | **M** (3–4d) | Deterministic steps + resume tokens |
| 2 | Plugin/skill registry (local + install) | TitanSafe foundation | **M** (5–7d) | Local registry first |
| 3 | Shell/SSH execution w/ approval | Jr. Dev discharge blocker | **M** (2–3d) | Allowlists + audit |
| 4 | Multi‑agent routing | Two‑son coordination | **M** (4–5d) | Delegation + routing rules |
| 5 | Context compression | Responsiveness + cost | **S** (1d) | Summaries + cache |
| 6 | Telemetry surface | Ops visibility | **S** (1–2d) | Queue/DLQ/cache stats |
| 7 | Memory upgrades (provider interface) | Durable recall | **M** (2–4d) | backend abstraction |
| 8 | MQTT bridge | Event‑driven automation | **M** (2–3d) | inbound triggers |
| 9 | Home Assistant adapter | Real‑world control | **S** (1–2d) | REST client |
| 10 | Multi‑channel gateway | Expansion later | **L** (5–8d) | Matrix/Teams/Nostr |
| 11 | Voice/TTS + STT | Showcase UX | **L** (5–7d) | optional |
| 12 | Browser automation | Optional power | **L** (5–8d) | Playwright/Stagehand |

Effort scale: **S**=1–2 days, **M**=3–7 days, **L**=1–2 weeks.

---

# Phase Plan

## Phase 1 — Unblock Discharge (Exec + Plugins)
**Goal:** Both Jr. Devs can receive tasks, produce reviewable code, and pass discharge criteria.

**Modules / Workstreams**
- **Workflow Pipelines** (Owner: CX)
  - Step runner with approval gates and resume tokens.
- **Shell/SSH Exec** (Owner: Flow)
  - Allowlist policy + approval middleware + audit trail.
- **Plugin/Skill Registry (Local)** (Owner: CC)
  - Local registry, install command, SDK skeleton.
- **Multi‑Agent Routing** (Owner: CX)
  - Delegation rules + per‑agent sessions.

**Acceptance Tests**
- Pipeline step pauses and resumes correctly with approval token.
- SSH command blocked when not on allowlist.
- Plugin installs from local registry and exposes tool.
- Flow → Ollie delegation produces a valid response.

**Pipeline Tasks**
- Build `pipeline_runner` (deterministic steps, resume).
- Implement `exec_gateway` with approval logging.
- Add `titanflow plugin install <name>` + manifest schema.
- Add routing rules + session binding.

---

## Phase 2 — TitanSafe Marketplace Features
**Goal:** Verified plugin directory and publish/verify workflow.

**Modules / Workstreams**
- **Marketplace Registry** (Owner: CC)
  - Index + metadata schema + compatibility matrix.
- **Verification Harness** (Owner: CX)
  - Static checks + integration tests + badges.
- **Release Automation** (Owner: Flow)
  - Versioning + publish pipeline + changelog.

**Acceptance Tests**
- Unverified plugin cannot be published.
- Verified plugin shows badge + install docs.
- CI rejects unsafe plugin contents.

**Pipeline Tasks**
- Build TitanSafe registry schema.
- Implement verification CI step.
- Generate install docs + compatibility badges.

---

## Phase 3 — “What’s Next?” Showcase Features
**Goal:** Demonstrate the ecosystem’s power with visible, user‑facing wins.

**Modules / Workstreams**
- **Context Compression** (Owner: CX)
- **Telemetry Surface** (Owner: CC)
- **Memory Provider Upgrade** (Owner: Flow)
- **MQTT Bridge** (Owner: Flow)
- **Home Assistant Adapter** (Owner: CX)
- **Multi‑Channel Gateway** (Owner: CC)
- **Voice / TTS / STT** (Owner: Flow)

**Acceptance Tests**
- Prompts shrink without breaking answers.
- `/metrics` shows queue, DLQ, cache, lag.
- Memory recall works after restart.
- MQTT event triggers task creation.
- HA device state query returns JSON.
- A second channel can send/receive messages.
- Voice request produces spoken reply.

**Pipeline Tasks**
- Add compression hooks in LLM broker.
- Add telemetry exporters + dashboard widgets.
- Add provider interface for memory.
- Add MQTT + HA modules.
- Add multi‑channel adapters.
- Add voice pipeline (STT → LLM → TTS).

---

## Notes
- Phase 1 is the discharge blocker.
- Phase 2 builds TitanSafe marketplace as a first‑class product.
- Phase 3 is a public‑facing showcase once the core is stable.
