/**
 * OCTA CONTEXT ENGINE — context-db.js
 * Local SQLite persistence for all agent conversations.
 * 
 * Every message from every agent gets saved here.
 * On /new session, the loader queries this DB to build
 * the briefing that gets injected into the opening system prompt.
 * 
 * Schema designed for lean reads — indexed on agent + timestamp.
 */

const Database = require('better-sqlite3');
const path = require('path');
const os = require('os');
const fs = require('fs');

// Default DB path — can override via TITANFLOW_CONTEXT_DB / OCTA_CONTEXT_DB env var
const DEFAULT_DB_PATH = path.join(os.homedir(), '.octa', 'context.db');

class ContextDB {
  constructor(dbPath = null) {
    this.dbPath =
      dbPath ||
      process.env.TITANFLOW_CONTEXT_DB ||
      process.env.OCTA_CONTEXT_DB ||
      DEFAULT_DB_PATH;
    this._ensureDir();
    this.db = new Database(this.dbPath);
    this.db.pragma('journal_mode = WAL');   // concurrent reads without locking
    this.db.pragma('synchronous = NORMAL'); // safe but fast
    this._init();
  }

  _ensureDir() {
    const dir = path.dirname(this.dbPath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  }

  _init() {
    this.db.exec(`
      -- Every message from every agent
      CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id    TEXT    NOT NULL,           -- 'ollie','cl','ac','cc','cx','flow' etc.
        agent_name  TEXT,                       -- display name if set
        surface     TEXT    DEFAULT 'octa',     -- product/runtime surface: octa|atlas|flow|dash|nook
        session_id  TEXT    NOT NULL,           -- UUID of the conversation session
        role        TEXT    NOT NULL,           -- 'user' | 'assistant' | 'system'
        content     TEXT    NOT NULL,
        tokens_est  INTEGER DEFAULT 0,          -- rough token estimate for budget tracking
        timestamp   INTEGER NOT NULL,           -- unix ms
        metadata    TEXT    DEFAULT '{}'        -- JSON: model, temp, any extras
      );

      -- Sessions metadata
      CREATE TABLE IF NOT EXISTS sessions (
        session_id   TEXT    PRIMARY KEY,
        agent_id     TEXT    NOT NULL,
        surface      TEXT    DEFAULT 'octa',
        started_at   INTEGER NOT NULL,
        ended_at     INTEGER,
        message_count INTEGER DEFAULT 0,
        summary      TEXT,                      -- auto-generated summary of this session
        summarized   INTEGER DEFAULT 0          -- 0 = raw, 1 = summarized
      );

      -- Agent registry (maps agent_id → config)
      CREATE TABLE IF NOT EXISTS agents (
        agent_id     TEXT PRIMARY KEY,
        display_name TEXT,
        color        TEXT,   -- hex, for UI
        last_seen    INTEGER,
        total_messages INTEGER DEFAULT 0
      );

      -- Summaries table (compressed older context)
      CREATE TABLE IF NOT EXISTS summaries (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id     TEXT    NOT NULL,
        period_start INTEGER NOT NULL,
        period_end   INTEGER NOT NULL,
        summary_text TEXT    NOT NULL,
        source_count INTEGER DEFAULT 0,   -- how many messages were summarized
        created_at   INTEGER NOT NULL
      );

      -- Indexes for fast lookups
      CREATE INDEX IF NOT EXISTS idx_messages_agent_time
        ON messages(agent_id, timestamp DESC);

      CREATE INDEX IF NOT EXISTS idx_messages_surface_time
        ON messages(surface, timestamp DESC);

      CREATE INDEX IF NOT EXISTS idx_messages_session
        ON messages(session_id, timestamp ASC);

      CREATE INDEX IF NOT EXISTS idx_sessions_agent_surface
        ON sessions(agent_id, surface, started_at DESC);

      CREATE INDEX IF NOT EXISTS idx_summaries_agent_time
        ON summaries(agent_id, period_start DESC);
    `);

    // Runtime-safe schema migration for existing DBs.
    this._migrateSchema();

    // Seed known agents if not present
    const knownAgents = [
      { id: 'ollie', name: 'Ollie', color: '#1e90ff' },
      { id: 'cl',    name: 'CL',    color: '#ff7b00' },
      { id: 'ac',    name: 'AC',    color: '#00d47a' },
      { id: 'cc',    name: 'CC',    color: '#8b5cf6' },
      { id: 'cx',    name: 'CX',    color: '#00d4ff' },
      { id: 'flow',  name: 'Flow',  color: '#6b7d99' },
    ];
    const upsertAgent = this.db.prepare(`
      INSERT INTO agents (agent_id, display_name, color, last_seen)
      VALUES (@id, @name, @color, @now)
      ON CONFLICT(agent_id) DO NOTHING
    `);
    const now = Date.now();
    for (const a of knownAgents) {
      upsertAgent.run({ ...a, now });
    }
  }

  // ─── WRITE ──────────────────────────────────────────────────────────────

  /**
   * Save a single message to the DB.
   * Call this on EVERY message in EVERY conversation.
   */
  saveMessage({ agentId, agentName, surface = 'octa', sessionId, role, content, metadata = {} }) {
    const tokens = this._estimateTokens(content);
    const ts = Date.now();

    const insert = this.db.prepare(`
      INSERT INTO messages (agent_id, agent_name, surface, session_id, role, content, tokens_est, timestamp, metadata)
      VALUES (@agentId, @agentName, @surface, @sessionId, @role, @content, @tokens, @ts, @meta)
    `);

    const updateAgent = this.db.prepare(`
      INSERT INTO agents (agent_id, display_name, color, last_seen, total_messages)
      VALUES (@id, @name, '#888', @ts, 1)
      ON CONFLICT(agent_id) DO UPDATE SET
        last_seen = @ts,
        total_messages = total_messages + 1,
        display_name = COALESCE(@name, display_name)
    `);

    const upsertSession = this.db.prepare(`
      INSERT INTO sessions (session_id, agent_id, surface, started_at, message_count)
      VALUES (@sessionId, @agentId, @surface, @ts, 1)
      ON CONFLICT(session_id) DO UPDATE SET
        message_count = message_count + 1,
        ended_at = @ts
    `);

    const run = this.db.transaction(() => {
      insert.run({
        agentId,
        agentName: agentName || agentId,
        surface: this._normalizeSurface(surface),
        sessionId,
        role,
        content,
        tokens: tokens,
        ts,
        meta: JSON.stringify(metadata),
      });
      updateAgent.run({ id: agentId, name: agentName, ts });
      upsertSession.run({ sessionId, agentId, surface: this._normalizeSurface(surface), ts });
    });

    run();
    return { saved: true, tokens, timestamp: ts };
  }

  /**
   * Mark a session as ended (call when /new is triggered)
   */
  closeSession(sessionId, surface = null) {
    if (surface) {
      this.db.prepare(`
        UPDATE sessions SET ended_at = ? WHERE session_id = ? AND surface = ?
      `).run(Date.now(), sessionId, this._normalizeSurface(surface));
      return;
    }
    this.db.prepare(`UPDATE sessions SET ended_at = ? WHERE session_id = ?`).run(Date.now(), sessionId);
  }

  /**
   * Save a summary of a time period (used by the auto-summarizer)
   */
  saveSummary({ agentId, periodStart, periodEnd, summaryText, sourceCount }) {
    this.db.prepare(`
      INSERT INTO summaries (agent_id, period_start, period_end, summary_text, source_count, created_at)
      VALUES (@agentId, @periodStart, @periodEnd, @summaryText, @sourceCount, @now)
    `).run({ agentId, periodStart, periodEnd, summaryText, sourceCount, now: Date.now() });
  }

  // ─── READ ───────────────────────────────────────────────────────────────

  /**
   * Get raw messages for an agent within a time window.
   * Default: last 60 minutes.
   */
  getRecentMessages(agentId, windowMs = 60 * 60 * 1000, maxMessages = 200, surface = null) {
    const since = Date.now() - windowMs;
    if (surface) {
      return this.db.prepare(`
        SELECT id, role, content, tokens_est, timestamp, session_id, agent_name, surface
        FROM messages
        WHERE agent_id = ? AND timestamp >= ? AND surface = ?
        ORDER BY timestamp ASC
        LIMIT ?
      `).all(agentId, since, this._normalizeSurface(surface), maxMessages);
    }
    return this.db.prepare(`
      SELECT id, role, content, tokens_est, timestamp, session_id, agent_name, surface
      FROM messages
      WHERE agent_id = ? AND timestamp >= ?
      ORDER BY timestamp ASC
      LIMIT ?
    `).all(agentId, since, maxMessages);
  }

  /**
   * Get ALL agents' recent messages merged and sorted.
   * Useful for Papa's context — see what ALL agents did recently.
   */
  getAllAgentsRecent(windowMs = 60 * 60 * 1000, maxMessages = 300, surface = null) {
    const since = Date.now() - windowMs;
    if (surface) {
      return this.db.prepare(`
        SELECT m.id, m.agent_id, m.agent_name, m.surface, m.role, m.content,
               m.tokens_est, m.timestamp, m.session_id
        FROM messages m
        WHERE m.timestamp >= ? AND m.surface = ?
        ORDER BY m.timestamp ASC
        LIMIT ?
      `).all(since, this._normalizeSurface(surface), maxMessages);
    }
    return this.db.prepare(`
      SELECT m.id, m.agent_id, m.agent_name, m.surface, m.role, m.content,
             m.tokens_est, m.timestamp, m.session_id
      FROM messages m
      WHERE m.timestamp >= ?
      ORDER BY m.timestamp ASC
      LIMIT ?
    `).all(since, maxMessages);
  }

  /**
   * Get the most recent N messages for an agent (ignores time window).
   * Good fallback when not much happened in the last hour.
   */
  getLastNMessages(agentId, n = 50, surface = null) {
    if (surface) {
      return this.db.prepare(`
        SELECT id, role, content, tokens_est, timestamp, session_id, surface
        FROM messages
        WHERE agent_id = ? AND surface = ?
        ORDER BY timestamp DESC
        LIMIT ?
      `).all(agentId, this._normalizeSurface(surface), n).reverse();
    }
    return this.db.prepare(`
      SELECT id, role, content, tokens_est, timestamp, session_id, surface
      FROM messages
      WHERE agent_id = ?
      ORDER BY timestamp DESC
      LIMIT ?
    `).all(agentId, n).reverse();
  }

  /**
   * Get recent summaries for an agent (compressed older context).
   */
  getRecentSummaries(agentId, limit = 5) {
    return this.db.prepare(`
      SELECT summary_text, period_start, period_end, source_count
      FROM summaries
      WHERE agent_id = ?
      ORDER BY period_start DESC
      LIMIT ?
    `).all(agentId, limit);
  }

  /**
   * Get total token budget used in the last window.
   * Used to decide whether to inject raw or summarized.
   */
  getTokenCount(agentId, windowMs = 60 * 60 * 1000, surface = null) {
    const since = Date.now() - windowMs;
    if (surface) {
      const scoped = this.db.prepare(`
        SELECT SUM(tokens_est) as total FROM messages
        WHERE agent_id = ? AND timestamp >= ? AND surface = ?
      `).get(agentId, since, this._normalizeSurface(surface));
      return scoped?.total || 0;
    }
    const result = this.db.prepare(`
      SELECT SUM(tokens_est) as total FROM messages
      WHERE agent_id = ? AND timestamp >= ?
    `).get(agentId, since);
    return result?.total || 0;
  }

  /**
   * Get all sessions for an agent in a time window.
   */
  getRecentSessions(agentId, windowMs = 24 * 60 * 60 * 1000, surface = null) {
    const since = Date.now() - windowMs;
    if (surface) {
      return this.db.prepare(`
        SELECT session_id, surface, started_at, ended_at, message_count, summary
        FROM sessions
        WHERE agent_id = ? AND started_at >= ? AND surface = ?
        ORDER BY started_at DESC
      `).all(agentId, since, this._normalizeSurface(surface));
    }
    return this.db.prepare(`
      SELECT session_id, surface, started_at, ended_at, message_count, summary
      FROM sessions
      WHERE agent_id = ? AND started_at >= ?
      ORDER BY started_at DESC
    `).all(agentId, since);
  }

  /**
   * Get agent registry info.
   */
  getAgent(agentId) {
    return this.db.prepare(
      'SELECT * FROM agents WHERE agent_id = ?'
    ).get(agentId);
  }

  getAllAgents() {
    return this.db.prepare(
      'SELECT * FROM agents ORDER BY last_seen DESC'
    ).all();
  }

  /**
   * Stats for a given agent — useful for the Octa UI.
   */
  getStats(agentId, surface = null) {
    const normalizedSurface = surface ? this._normalizeSurface(surface) : null;
    const agent = this.getAgent(agentId);
    const recentTokens = this.getTokenCount(agentId, 60 * 60 * 1000, normalizedSurface);
    const messageCount = normalizedSurface
      ? this.db.prepare('SELECT COUNT(*) as c FROM messages WHERE agent_id = ? AND surface = ?')
          .get(agentId, normalizedSurface)
      : this.db.prepare('SELECT COUNT(*) as c FROM messages WHERE agent_id = ?').get(agentId);
    const sessionCount = normalizedSurface
      ? this.db.prepare('SELECT COUNT(*) as c FROM sessions WHERE agent_id = ? AND surface = ?')
          .get(agentId, normalizedSurface)
      : this.db.prepare('SELECT COUNT(*) as c FROM sessions WHERE agent_id = ?').get(agentId);
    return {
      agentId,
      surface: normalizedSurface,
      displayName: agent?.display_name || agentId,
      totalMessages: messageCount?.c || agent?.total_messages || 0,
      lastSeen: agent?.last_seen,
      recentTokens1h: recentTokens,
      totalSessions: sessionCount?.c || 0,
    };
  }

  // ─── CLEANUP ────────────────────────────────────────────────────────────

  /**
   * Prune messages older than retentionDays.
   * Run this on a daily cron — keeps the DB lean.
   */
  pruneOld(retentionDays = 90) {
    const cutoff = Date.now() - (retentionDays * 24 * 60 * 60 * 1000);
    const result = this.db.prepare(
      'DELETE FROM messages WHERE timestamp < ?'
    ).run(cutoff);
    this.db.prepare(
      'DELETE FROM sessions WHERE started_at < ? AND ended_at IS NOT NULL'
    ).run(cutoff);
    // Vacuum once in a while to reclaim space
    if (Math.random() < 0.1) this.db.exec('VACUUM');
    return result.changes;
  }

  // ─── UTILS ──────────────────────────────────────────────────────────────

  /**
   * Very rough token estimator — ~4 chars per token.
   * Good enough for budget decisions, no need to be exact.
   */
  _estimateTokens(text) {
    return Math.ceil((text || '').length / 4);
  }

  _normalizeSurface(surface) {
    if (!surface) return 'octa';
    return String(surface).trim().toLowerCase();
  }

  _hasColumn(tableName, columnName) {
    const rows = this.db.prepare(`PRAGMA table_info(${tableName})`).all();
    return rows.some((r) => r.name === columnName);
  }

  _migrateSchema() {
    if (!this._hasColumn('messages', 'surface')) {
      this.db.exec(`ALTER TABLE messages ADD COLUMN surface TEXT DEFAULT 'octa'`);
    }
    if (!this._hasColumn('sessions', 'surface')) {
      this.db.exec(`ALTER TABLE sessions ADD COLUMN surface TEXT DEFAULT 'octa'`);
    }
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_messages_surface_time
        ON messages(surface, timestamp DESC);
    `);
    this.db.exec(`
      CREATE INDEX IF NOT EXISTS idx_sessions_agent_surface
        ON sessions(agent_id, surface, started_at DESC);
    `);
  }

  close() {
    this.db.close();
  }
}

module.exports = ContextDB;
