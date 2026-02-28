# TitanFlow v0.2 MVP — Scaffold + Tasks (Research + IPC HTTP + Supervisor Alert)

## Summary
This document defines the MVP implementation path for TitanFlow v0.2 (microkernel architecture). MVP includes:
- Core kernel process (IPC, LLM broker, DB broker, HTTP proxy)
- Research module moved to IPC
- Module supervisor alert (Telegram to Papa) on module death
- Systemd unit templates + manifests

Non‑MVP items (newspaper, codeexec, auto‑restart, remote modules) are explicitly deferred.

---

## File/Folder Scaffold (MVP)
```
titanflow/
├── core/
│   ├── kernel.py
│   ├── ipc.py
│   ├── llm_broker.py
│   ├── auth.py
│   ├── database_broker.py
│   ├── http_proxy.py
│   ├── module_supervisor.py
│   ├── audit.py
│   └── config.py
├── modules/
│   ├── base_ipc.py
│   └── research/module.py
├── manifests/
│   └── research.yaml
├── config/
│   └── titanflow-core.yaml
├── deploy/systemd/
│   ├── titanflow-core.service
│   └── titanflow-mod@.service
└── docs/
    └── titanflow-v0.2-mvp-scaffold.md
```

---

## MVP Tasks

### 1) Core Config
- Add `config/titanflow-core.yaml` with core + telegram + llm + db + http_proxy settings.
- Load via `TITANFLOW_CORE_CONFIG`.

### 2) IPC Server
- JSON‑lines over Unix socket.
- Methods: `auth.register`, `llm.generate`, `db.query`, `db.insert`, `db.update`, `http.request`, `audit.log`.
- Session tokens only after handshake.

### 3) HTTP Proxy (Core)
- `http.request` method in Core using `httpx` + retries.
- Validate domains using manifest allowlist.

### 4) LLM Broker
- Priority queue (chat > module > research).
- All LLM usage via broker.

### 5) DB Broker
- Async wrapper over sqlite (threadpool).
- Enforce `busy_timeout`, WAL, row limits.

### 6) Module Supervisor (Alert only)
- Track module connection/heartbeat.
- If module disconnects → Telegram alert to Papa.

### 7) Research Module (IPC client)
- No AF_INET.
- Use `http.request` for RSS/GitHub fetches.
- Use `db.query/insert/update` for persistence.
- Use `llm.generate` for summaries.

### 8) Manifests + Tokens
- `manifests/research.yaml` defines allowed domains/tables.
- Token at `/etc/titanflow/secrets/research.token`.

### 9) Systemd Templates
- `titanflow-core.service`: network + IPC.
- `titanflow-mod@.service`: AF_UNIX only.

---

## Acceptance Tests (MVP)
- Core starts and listens on socket.
- Research module auth handshake succeeds.
- `http.request` works for allowed domains.
- `http.request` denied for unlisted domain.
- Kill research module → Telegram alert to Papa.
- Chat preempts research in LLM broker.

---

## Deferred (Explicitly Out of MVP)
- Auto‑restart policies/backoff
- Newspaper + CodeExec IPC migrations
- Remote module connectivity
- Web dashboard

---

## Notes
This MVP prioritizes security model correctness (no AF_INET in Research) and operator visibility (module death alert). It does not attempt to enforce full module lifecycle or remote module federation yet.
