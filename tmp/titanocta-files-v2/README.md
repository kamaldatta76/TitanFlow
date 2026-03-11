# OCTA CONTEXT ENGINE
## Persistent memory for all agents — local SQLite, zero cloud dependency

---

### What this does

Every message from every agent (Ollie, CL, AC, CC, CX, Flow) gets saved to a local SQLite DB on TitanShadow.

When `/new` fires — instead of amnesia — the agent gets a **lean briefing** injected at the top of the new session's system prompt:

```
━━━ OCTA CONTEXT BRIEFING ━━━
Agent: OLLIE | Resumed: 11/03/2026, 22:14

[Last session · started 47m ago]
21:28 Papa: let's fix the VLAN config on TitanStrike
21:29 Ollie: checking OPNsense interface assignments now...
21:31 Papa: good, now CX needs to be on Octa
21:33 Ollie: moving CX to the Octa inference pool...

━━━ CONTINUE FROM HERE ━━━
You remember everything above.
```

**Token budget logic:**
- `< 1,500 tokens` of history → inject raw (full verbatim)
- `1,500 – 4,000 tokens` → inject compressed (meaningful exchanges only)
- `> 4,000 tokens` → inject summary only (digest + last few lines)

Live session stays lean. Context never goes stale. Agents never lose the thread.

---

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    TitanShadow (10.0.0.x)               │
│                                                         │
│   TitanFlow Gateway                                      │
│   ├── titanflow-hook.js  ──saves──►  octa-context:7474  │
│   │   (every message)              └── context.db       │
│   │                                    (SQLite, local)  │
│   └── on /new ──────────fetches──►  GET /briefing/ollie │
│       injects into                      └── returns     │
│       system prompt                         lean text   │
│                                                         │
│   Same service handles ALL agents:                      │
│   ollie / cl / ac / cc / cx / flow                      │
└─────────────────────────────────────────────────────────┘
```

---

### Deploy

**On TitanShadow:**

```bash
# 1. Copy files
scp -r octa-context/ root@10.0.0.x:/root/octa-context

# Or via Mercury jump:
ssh kamaldatta@10.0.0.33 "ssh root@23.111.137.106 'mkdir -p /root/octa-context'"
# then scp each file

# 2. Install deps
cd /root/octa-context
npm install

# 3. Generate auth token
openssl rand -hex 32
# → paste result into octa-context.service Environment=OCTA_CONTEXT_TOKEN=

# 4. Install and start service
cp octa-context.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable octa-context
systemctl start octa-context
systemctl status octa-context

# 5. Test it's alive
curl http://127.0.0.1:7474/health
# → {"ok":true,"service":"octa-context","uptime":...}

# 6. Wire TitanFlow hook
cp titanflow-hook.js ~/.titanflow/hooks/octa-context-hook.js

# Add these to TitanFlow's env (wherever it reads env from):
# TITANFLOW_CONTEXT_API=http://127.0.0.1:7474
# TITANFLOW_CONTEXT_TOKEN=<your token from step 3>
# TITANFLOW_AGENT_ID=ollie   ← change per agent
# TITANFLOW_CONTEXT_SURFACE=octa
# (Legacy OCTA_* env vars are still accepted for compatibility)

# 7. Restart TitanFlow
systemctl restart titanflow  # or however you restart it
```

---

### API reference

```
POST /message
  Body: { agentId, agentName?, sessionId, role, content, metadata? }
  → Saves a message. Call on every exchange.

POST /session/close
  Body: { sessionId }
  → Marks session ended. Call when /new fires.

GET /briefing/:agentId?window=3600000&others=false&maxTokens=800
  → Returns systemContext string to inject. THE KEY ENDPOINT.

GET /stats/:agentId
  → Agent stats (total messages, last seen, recent tokens)

GET /agents
  → All agents with last-seen info

GET /recent/:agentId?window=3600000&limit=50
  → Raw recent messages (for Octa UI)

GET /feed?window=3600000
  → All agents merged timeline (Papa view)

POST /summary
  Body: { agentId, summaryText, periodStart?, periodEnd?, sourceCount? }
  → Save a compressed summary of a period

POST /admin/prune
  Body: { days: 90 }
  → Delete messages older than N days

GET /health
  → Health check
```

---

### For Octa Desktop app (future)

The Octa desktop/iPad app will call `/briefing/:agentId` on launch
and show a "Picked up from X minutes ago" indicator.

The `/feed` endpoint powers the "All agents" merged timeline view —
Papa can see what every agent was doing in the last hour from one panel.

---

### Per-agent config

Each agent that uses this needs its own `OCTA_AGENT_ID` set.
One context server can handle all agents — they're separated in the DB by `agent_id`.

Current roster: `ollie` · `cl` · `ac` · `cc` · `cx` · `flow`

---

### Daily cron (prune + auto-summarize)

```bash
# Add to crontab on TitanShadow:
# 3am daily — prune messages older than 90 days
0 3 * * * curl -s -X POST http://127.0.0.1:7474/admin/prune \
  -H "x-octa-token: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"days":90}' >> /var/log/octa-context-prune.log 2>&1
```

---

### DB location

`/root/.octa/context.db` on TitanShadow.

Back this up with your existing age-encrypted SQLite backup system —
it's already discovering DBs under `~/.titanflow/`.
Add `~/.octa/` to the discovery path.
