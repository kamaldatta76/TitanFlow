/**
 * OCTA CONTEXT ENGINE — titanflow-hook.js
 * 
 * TitanFlow hook. Drop this in your TitanFlow hooks directory
 * and it automatically wires up context saving + briefing injection.
 * 
 * What it does:
 *   1. On every message_received → saves to context DB
 *   2. On every message_sent     → saves to context DB
 *   3. On session_start          → fetches briefing and injects into system prompt
 *   4. On session_end            → closes the session in the DB
 * 
 * Install:
 *   cp titanflow-hook.js ~/.titanflow/hooks/octa-context-hook.js
 *   Then restart TitanFlow.
 */

const CONTEXT_API =
  process.env.TITANFLOW_CONTEXT_API ||
  process.env.OCTA_CONTEXT_API ||
  'http://127.0.0.1:7474';
const CONTEXT_TOKEN =
  process.env.TITANFLOW_CONTEXT_TOKEN ||
  process.env.OCTA_CONTEXT_TOKEN ||
  '';
const AGENT_ID =
  process.env.TITANFLOW_AGENT_ID ||
  process.env.OCTA_AGENT_ID ||
  'ollie';
const SURFACE =
  process.env.TITANFLOW_CONTEXT_SURFACE ||
  process.env.OCTA_CONTEXT_SURFACE ||
  'octa';

// In-memory session tracking (session IDs per conversation)
const activeSessions = new Map();

/**
 * Gets or creates a session ID for a conversation.
 */
function getSessionId(conversationId) {
  if (!activeSessions.has(conversationId)) {
    activeSessions.set(conversationId, generateId());
  }
  return activeSessions.get(conversationId);
}

function generateId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 9);
}

/**
 * POST to context API — fire and forget on message save.
 * Never blocks the main conversation flow.
 */
async function saveMessage(payload) {
  try {
    await fetch(`${CONTEXT_API}/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-octa-surface': SURFACE,
        ...(CONTEXT_TOKEN ? { 'x-octa-token': CONTEXT_TOKEN } : {}),
      },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    // Never crash TitanFlow — context is best-effort
    console.warn('[octa-context] save failed (non-fatal):', e.message);
  }
}

/**
 * GET briefing from context API — called on new session start.
 * Returns the systemContext string or null.
 */
async function fetchBriefing(agentId) {
  try {
    const res = await fetch(
      `${CONTEXT_API}/briefing/${agentId}?window=3600000&others=false&surface=${encodeURIComponent(SURFACE)}`,
      {
        headers: {
          ...(CONTEXT_TOKEN ? { 'x-octa-token': CONTEXT_TOKEN } : {}),
          'x-octa-surface': SURFACE,
        },
      }
    );
    if (!res.ok) return null;
    const data = await res.json();
    return data.systemContext || null;
  } catch (e) {
    console.warn('[octa-context] briefing failed (non-fatal):', e.message);
    return null;
  }
}

function normalizeConversationId(event) {
  return (
    event.conversationId ||
    event.conversation_id ||
    event.sessionId ||
    event.session_id ||
    event.chatId ||
    event.chat_id ||
    `${AGENT_ID}-default`
  );
}

function extractContent(event) {
  if (typeof event.content === 'string') return event.content;
  if (typeof event.message === 'string') return event.message;
  if (typeof event.output === 'string') return event.output;
  if (typeof event.response === 'string') return event.response;
  if (event.message && typeof event.message.content === 'string') return event.message.content;
  if (event.response && typeof event.response.content === 'string') return event.response.content;
  return JSON.stringify(event.content ?? event.message ?? event.response ?? event.output ?? '');
}

async function handleMessageReceived(event) {
  const conversationId = normalizeConversationId(event);
  const sessionId = getSessionId(conversationId);

  saveMessage({
    agentId: AGENT_ID,
    agentName: event.agentName || event.agent_name || AGENT_ID,
    surface: SURFACE,
    sessionId,
    role: 'user',
    content: extractContent(event),
  });

  return event;
}

async function handleMessageSent(event) {
  const conversationId = normalizeConversationId(event);
  const sessionId = getSessionId(conversationId);

  saveMessage({
    agentId: AGENT_ID,
    agentName: event.agentName || event.agent_name || AGENT_ID,
    surface: SURFACE,
    sessionId,
    role: 'assistant',
    content: extractContent(event),
  });

  return event;
}

async function handleSessionStart(event) {
  const conversationId = normalizeConversationId(event);
  const existingPrompt = event.systemPrompt || event.system_prompt || '';

  const sessionId = generateId();
  activeSessions.set(conversationId, sessionId);

  const briefing = await fetchBriefing(AGENT_ID);
  if (briefing) {
    const prompt = briefing + '\n\n---\n\n' + existingPrompt;
    event.systemPrompt = prompt;
    event.system_prompt = prompt;
    console.log(`[octa-context] Injected briefing for ${AGENT_ID} (session: ${sessionId}, surface: ${SURFACE})`);
  }

  return event;
}

async function handleSessionEnd(event) {
  const conversationId = normalizeConversationId(event);
  const sessionId = activeSessions.get(conversationId);

  if (sessionId) {
    try {
      await fetch(`${CONTEXT_API}/session/close`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-octa-surface': SURFACE,
          ...(CONTEXT_TOKEN ? { 'x-octa-token': CONTEXT_TOKEN } : {}),
        },
        body: JSON.stringify({ sessionId, surface: SURFACE }),
      });
    } catch (e) {
      console.warn('[octa-context] session close failed (non-fatal):', e.message);
    }
    activeSessions.delete(conversationId);
  }

  return event;
}

// ─── TITANFLOW HOOK EXPORTS ──────────────────────────────────────────────
// TitanFlow docs in-repo define message:before / message:after as canonical message hooks.
// Legacy underscore hooks are retained for compatibility on older gateways.

module.exports = {
  metadata: {
    name: 'octa-context',
    version: '1.1.1',
    description: 'Persistent context across sessions — saves all messages, injects briefing on /new',
    events: [
      'message:before', 'message:after',
      'message_received', 'message_sent', 'session_start', 'session_end',
    ],
  },

  // Canonical TitanFlow message hooks (docs)
  async on_message_before(event) { return handleMessageReceived(event); },
  async on_message_after(event) { return handleMessageSent(event); },

  /**
   * Fires when user sends a message to the agent.
   */
  async on_message_received(event) { return handleMessageReceived(event); },

  /**
   * Fires when the agent sends a response.
   */
  async on_message_sent(event) { return handleMessageSent(event); },

  /**
   * Fires when a new session starts (user hits /new or opens fresh chat).
   * THIS is where the magic happens — inject the briefing.
   */
  async on_session_start(event) { return handleSessionStart(event); },

  /**
   * Fires when a session ends.
   */
  async on_session_end(event) { return handleSessionEnd(event); },
};
