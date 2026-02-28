# TitanFlow Core — Grounding Gate + Memory Persistence (2026-02-28)

## Purpose
Implement a strict grounding gate for Telegram chat in Core (retrieve → decide → LLM only if sources exist), and persist chat history in Core so context survives reboots. This addresses out-of-context hallucinations ("GMS" issue) and removes stateless ambiguity.

## Scope
- Telegram routing (Core): decide if grounding required, search DB, refuse on no hits, otherwise answer with citations.
- Structured LLM output for grounded queries: JSON `{answer, citations, refusal}` with validation.
- DB search API: search `feed_items`, `research_summaries`, `articles` (LIKE now; FTS later).
- Audit: log gate decisions (`gate`, `hits`, `decision`).
- Persist chat history: `conversations` + `messages` tables.
- Rebuild context window: last N turns + pinned system directives.
- Memory status: truthful reply when asked.
- Prompt tweak: never claim stateless; use Core memory wording.

## Acceptance Checks
- “Who is GMS?” with no DB row → refusal, no LLM call.
- Insert DB row for GMS → answer with citations.
- Chat remains responsive during research (LLM priority queue).

## Notes
- DB path remains `/data/titanflow/titanflow.db` for persistence.
- Pinned directives table added (empty by default).
- Gate applies to Telegram chat only; modules continue via IPC.
