/**
 * OCTA CONTEXT ENGINE — context-loader.js
 * 
 * This is the brain of the system.
 * 
 * When /new fires, this gets called. It:
 * 1. Queries the DB for the last hour of that agent's history
 * 2. Decides whether to inject raw messages or summarized briefing
 *    based on token budget (raw if lean, summarized if heavy)
 * 3. Returns a formatted context string ready to be prepended
 *    to the new session's system prompt
 * 
 * Token budget logic:
 *   < 1,500 tokens  → inject last hour raw (readable, immediate)
 *   1,500 – 4,000   → inject compressed format (key exchanges only)
 *   > 4,000         → inject summary only (call summarizer first)
 * 
 * The goal: agent opens new session and already knows what just happened.
 * Feels like the conversation never stopped.
 */

const ContextDB = require('./context-db');

// Token budgets
const RAW_THRESHOLD       = 1500;
const COMPRESSED_THRESHOLD = 4000;
const MAX_INJECT_TOKENS   = 800;  // hard ceiling on what we inject into system prompt

class ContextLoader {
  constructor(db = null) {
    this.db = db || new ContextDB();
  }

  /**
   * Main entry point.
   * Call this when a new session starts for an agent.
   * 
   * Returns { systemContext: string, tokenCount: number, strategy: string }
   * Inject systemContext as a prefix to the system prompt.
   */
  async buildBriefing(agentId, options = {}) {
    const {
      windowMs      = 60 * 60 * 1000,  // last hour default
      maxTokens     = MAX_INJECT_TOKENS,
      includeOthers = false,            // include other agents' activity too
      surface       = null,             // scope context to a specific product surface
    } = options;

    // 1. Check how much happened in the window
    const recentTokens = this.db.getTokenCount(agentId, windowMs, surface);

    // 2. Also grab recent summaries (compressed older context)
    const oldSummaries = this.db.getRecentSummaries(agentId, 3);

    // 3. Choose injection strategy
    let strategy;
    let contextBlock;

    if (recentTokens === 0) {
      // Nothing happened recently — check if there's anything at all
      const lastMessages = this.db.getLastNMessages(agentId, 10, surface);
      if (lastMessages.length === 0) {
        return { systemContext: null, tokenCount: 0, strategy: 'none' };
      }
      strategy = 'last_n_fallback';
      contextBlock = this._buildRawBlock(lastMessages, agentId, maxTokens);
    } else if (recentTokens < RAW_THRESHOLD) {
      strategy = 'raw';
      const messages = this.db.getRecentMessages(agentId, windowMs, 200, surface);
      contextBlock = this._buildRawBlock(messages, agentId, maxTokens);
    } else if (recentTokens < COMPRESSED_THRESHOLD) {
      strategy = 'compressed';
      const messages = this.db.getRecentMessages(agentId, windowMs, 100, surface);
      contextBlock = this._buildCompressedBlock(messages, agentId, maxTokens);
    } else {
      strategy = 'summary_only';
      // Heavy session — use the last stored summary, or compress on the fly
      const messages = this.db.getRecentMessages(agentId, windowMs, 60, surface);
      contextBlock = this._buildSummaryBlock(messages, oldSummaries, agentId, maxTokens);
    }

    // 4. Optionally add cross-agent activity (for Papa / Octa-level awareness)
    let crossAgentBlock = '';
    if (includeOthers) {
      const allRecent = this.db.getAllAgentsRecent(windowMs, 80, surface);
      const othersActivity = allRecent.filter(m => m.agent_id !== agentId);
      if (othersActivity.length > 0) {
        crossAgentBlock = this._buildCrossAgentBlock(othersActivity);
      }
    }

    // 5. Assemble the full briefing
    const systemContext = this._assembleSystemContext(
      agentId,
      contextBlock,
      crossAgentBlock,
      strategy
    );

    return {
      systemContext,
      tokenCount: this._estimateTokens(systemContext),
      strategy,
      recentTokens,
    };
  }

  // ─── BLOCK BUILDERS ─────────────────────────────────────────────────────

  /**
   * Raw injection — last hour verbatim, trimmed to fit token budget.
   * Used when the session was light and the raw context is valuable.
   */
  _buildRawBlock(messages, agentId, maxTokens) {
    if (!messages.length) return '';

    const lines = [];
    let tokenBudget = maxTokens;

    // Walk messages newest-first so we prioritize recent context
    const reversed = [...messages].reverse();
    const included = [];

    for (const msg of reversed) {
      const msgTokens = msg.tokens_est || this._estimateTokens(msg.content);
      if (tokenBudget - msgTokens < 0) break;
      tokenBudget -= msgTokens;
      included.unshift(msg); // prepend to keep chronological order
    }

    // Format
    const timeLabel = this._relativeTime(included[0]?.timestamp);
    lines.push(`[Last session · started ${timeLabel}]`);

    for (const msg of included) {
      const time = new Date(msg.timestamp).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', hour12: false
      });
      const roleLabel = msg.role === 'user' ? 'Papa' : (msg.agent_name || agentId.toUpperCase());
      const content = this._trimContent(msg.content, 300);
      lines.push(`${time} ${roleLabel}: ${content}`);
    }

    return lines.join('\n');
  }

  /**
   * Compressed injection — keep only the meaningful exchanges.
   * Strips short/acknowledgment messages, keeps substantive content.
   */
  _buildCompressedBlock(messages, agentId, maxTokens) {
    if (!messages.length) return '';

    // Filter out noise: very short messages, pure acks
    const meaningful = messages.filter(m => {
      const c = m.content.trim();
      if (c.length < 20) return false;
      const noise = ['ok', 'got it', 'sure', 'yes', 'no', 'thanks', 'done', 'noted'];
      if (noise.some(n => c.toLowerCase() === n)) return false;
      return true;
    });

    if (!meaningful.length) return this._buildRawBlock(messages.slice(-10), agentId, maxTokens);

    // Group by session
    const bySession = {};
    for (const m of meaningful) {
      if (!bySession[m.session_id]) bySession[m.session_id] = [];
      bySession[m.session_id].push(m);
    }

    const lines = [];
    let tokenBudget = maxTokens;
    const sessions = Object.values(bySession).reverse(); // newest session first

    lines.push('[Recent context — compressed]');

    for (const session of sessions) {
      if (session.length === 0) continue;
      const sessionStart = this._relativeTime(session[0].timestamp);
      lines.push(`\n— Session (${sessionStart}) —`);

      for (const msg of session.slice(-6)) { // last 6 exchanges per session
        const roleLabel = msg.role === 'user' ? 'Papa' : (msg.agent_name || agentId.toUpperCase());
        const content = this._trimContent(msg.content, 200);
        const line = `  ${roleLabel}: ${content}`;
        const lineTokens = this._estimateTokens(line);
        if (tokenBudget - lineTokens < 50) break;
        tokenBudget -= lineTokens;
        lines.push(line);
      }

      if (tokenBudget < 100) break;
    }

    return lines.join('\n');
  }

  /**
   * Summary-only — for heavy sessions.
   * Uses stored summaries + a bullet-point digest of the last few exchanges.
   */
  _buildSummaryBlock(messages, oldSummaries, agentId, maxTokens) {
    const lines = ['[Context digest — heavy session]'];

    // Add stored summaries first
    if (oldSummaries.length > 0) {
      lines.push('\nPrevious context (summarized):');
      for (const s of oldSummaries.slice(0, 2)) {
        const when = this._relativeTime(s.period_start);
        lines.push(`  [${when}] ${this._trimContent(s.summary_text, 300)}`);
      }
    }

    // Add last few exchanges raw
    if (messages.length > 0) {
      lines.push('\nLast few exchanges:');
      const last = messages.slice(-8);
      for (const msg of last) {
        const roleLabel = msg.role === 'user' ? 'Papa' : (msg.agent_name || agentId.toUpperCase());
        const content = this._trimContent(msg.content, 150);
        lines.push(`  ${roleLabel}: ${content}`);
      }
    }

    return lines.join('\n');
  }

  /**
   * Cross-agent activity — what the other agents were doing.
   * Keeps it very brief to save tokens.
   */
  _buildCrossAgentBlock(othersMessages) {
    if (!othersMessages.length) return '';

    // Group by agent, show last message only per agent
    const byAgent = {};
    for (const m of othersMessages) {
      byAgent[m.agent_id] = m; // last one wins per agent
    }

    const lines = ['\n[Other agents — recent activity]'];
    for (const [agentId, msg] of Object.entries(byAgent)) {
      const name = msg.agent_name || agentId.toUpperCase();
      const when = this._relativeTime(msg.timestamp);
      const content = this._trimContent(msg.content, 100);
      lines.push(`  ${name} (${when}): ${content}`);
    }

    return lines.join('\n');
  }

  /**
   * Final assembly — wraps everything in the system context block.
   */
  _assembleSystemContext(agentId, contextBlock, crossAgentBlock, strategy) {
    if (!contextBlock && !crossAgentBlock) return null;

    const lines = [
      '━━━ OCTA CONTEXT BRIEFING ━━━',
      `Agent: ${agentId.toUpperCase()} | Resumed: ${new Date().toLocaleString()}`,
      '',
    ];

    if (contextBlock) lines.push(contextBlock);
    if (crossAgentBlock) lines.push(crossAgentBlock);

    lines.push('');
    lines.push('━━━ CONTINUE FROM HERE ━━━');
    lines.push(
      'The above is your recent session history. ' +
      'Pick up where you left off. You remember everything above.'
    );

    return lines.join('\n');
  }

  // ─── UTILS ──────────────────────────────────────────────────────────────

  _trimContent(text, maxChars) {
    if (!text) return '';
    const cleaned = text.replace(/\n+/g, ' ').trim();
    if (cleaned.length <= maxChars) return cleaned;
    return cleaned.slice(0, maxChars - 3) + '...';
  }

  _relativeTime(ts) {
    if (!ts) return 'unknown';
    const diff = Date.now() - ts;
    const mins = Math.floor(diff / 60000);
    if (mins < 1)  return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)  return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  _estimateTokens(text) {
    return Math.ceil((text || '').length / 4);
  }
}

module.exports = ContextLoader;
