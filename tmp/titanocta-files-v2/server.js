/**
 * OCTA CONTEXT ENGINE — server.js
 * 
 * Lightweight Express API. TitanFlow hooks, Octa app,
 * and any agent gateway call into this.
 * 
 * Endpoints:
 *   POST /message          — save a message (call on every exchange)
 *   POST /session/close    — mark session ended (/new was hit)
 *   GET  /briefing/:agent  — get context briefing for new session
 *   GET  /stats/:agent     — agent stats for Octa UI
 *   GET  /agents           — all agents with last-seen info
 *   GET  /recent/:agent    — raw recent messages (for Octa UI/debug)
 *   POST /summary          — save a manual summary
 *   GET  /health           — health check
 * 
 * Run on localhost:7474 — only accessible from the array, never exposed public.
 */

const express = require('express');
const { v4: uuidv4 } = require('uuid');
const ContextDB = require('./context-db');
const ContextLoader = require('./context-loader');

const PORT = process.env.TITANFLOW_CONTEXT_PORT || process.env.OCTA_CONTEXT_PORT || 7474;
const HOST = process.env.TITANFLOW_CONTEXT_HOST || process.env.OCTA_CONTEXT_HOST || '127.0.0.1'; // loopback only
const DEFAULT_SURFACE = (
  process.env.TITANFLOW_CONTEXT_SURFACE ||
  process.env.OCTA_CONTEXT_SURFACE ||
  'octa'
).toLowerCase();

const db     = new ContextDB();
const loader = new ContextLoader(db);
const app    = express();

app.use(express.json({ limit: '2mb' }));

function getRequestedSurface(req) {
  const fromBody = req.body && typeof req.body.surface === 'string' ? req.body.surface : null;
  const fromQuery = typeof req.query?.surface === 'string' ? req.query.surface : null;
  const fromHeader = typeof req.headers['x-octa-surface'] === 'string' ? req.headers['x-octa-surface'] : null;
  const raw = fromBody || fromQuery || fromHeader || null;
  if (!raw) return null;
  return String(raw).trim().toLowerCase();
}

function resolveSurface(req, fallback = DEFAULT_SURFACE) {
  const requested = getRequestedSurface(req);
  const raw = requested || fallback;
  return String(raw).trim().toLowerCase();
}

// Very simple auth token — set TITANFLOW_CONTEXT_TOKEN (or legacy OCTA_CONTEXT_TOKEN) in env
const AUTH_TOKEN = process.env.TITANFLOW_CONTEXT_TOKEN || process.env.OCTA_CONTEXT_TOKEN;
app.use((req, res, next) => {
  if (!AUTH_TOKEN) return next(); // no token configured = dev mode
  const token = req.headers['x-octa-token'] || req.query.token;
  if (token !== AUTH_TOKEN) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  next();
});


// ─── SAVE MESSAGE ─────────────────────────────────────────────────────────
// Call this on EVERY message in EVERY agent session.
// 
// Body: { agentId, agentName?, sessionId, role, content, metadata? }
app.post('/message', (req, res) => {
  const { agentId, agentName, sessionId, role, content, metadata } = req.body;
  const surface = resolveSurface(req);

  if (!agentId || !sessionId || !role || !content) {
    return res.status(400).json({ error: 'agentId, sessionId, role, content required' });
  }

  if (!['user', 'assistant', 'system'].includes(role)) {
    return res.status(400).json({ error: 'role must be user | assistant | system' });
  }

  try {
    const result = db.saveMessage({ agentId, agentName, surface, sessionId, role, content, metadata });
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error('[context] save error:', err.message);
    res.status(500).json({ error: err.message });
  }
});


// ─── CLOSE SESSION ────────────────────────────────────────────────────────
// Call when /new is triggered — marks the session as ended.
// 
// Body: { sessionId }
app.post('/session/close', (req, res) => {
  const { sessionId } = req.body;
  const surface = resolveSurface(req);
  if (!sessionId) return res.status(400).json({ error: 'sessionId required' });

  try {
    db.closeSession(sessionId, surface);
    res.json({ ok: true, closed: sessionId, surface });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── GET BRIEFING ─────────────────────────────────────────────────────────
// THE KEY ENDPOINT. Call this when starting a new session.
// Returns the systemContext string to prepend to the system prompt.
// 
// Query params:
//   window    — ms lookback window (default 3600000 = 1hr)
//   others    — include other agents' activity (true/false)
//   maxTokens — max tokens to inject (default 800)
app.get('/briefing/:agentId', async (req, res) => {
  const { agentId } = req.params;
  const windowMs    = parseInt(req.query.window)    || 60 * 60 * 1000;
  const maxTokens   = parseInt(req.query.maxTokens) || 800;
  const includeOthers = req.query.others === 'true';
  const surface = resolveSurface(req);

  try {
    const result = await loader.buildBriefing(agentId, {
      windowMs,
      maxTokens,
      includeOthers,
      surface,
    });

    res.json({
      ok: true,
      agentId,
      surface,
      ...result,
      hasContext: !!result.systemContext,
    });
  } catch (err) {
    console.error('[context] briefing error:', err.message);
    res.status(500).json({ error: err.message });
  }
});


// ─── STATS ────────────────────────────────────────────────────────────────
app.get('/stats/:agentId', (req, res) => {
  const requestedSurface = getRequestedSurface(req);
  const surface = requestedSurface || DEFAULT_SURFACE;
  try {
    const stats = db.getStats(req.params.agentId, surface);
    res.json({
      ok: true,
      surface,
      surfaceMode: requestedSurface ? 'scoped' : 'default-scoped',
      requestedSurface: requestedSurface || null,
      ...stats,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── ALL AGENTS ──────────────────────────────────────────────────────────
app.get('/agents', (req, res) => {
  try {
    const agents = db.getAllAgents();
    res.json({ ok: true, agents });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── RECENT MESSAGES ─────────────────────────────────────────────────────
// For Octa UI display — shows the live feed of what's been said.
app.get('/recent/:agentId', (req, res) => {
  const windowMs  = parseInt(req.query.window) || 60 * 60 * 1000;
  const limit     = parseInt(req.query.limit)  || 50;
  const surface = resolveSurface(req);
  try {
    const messages = db.getRecentMessages(req.params.agentId, windowMs, limit, surface);
    res.json({ ok: true, surface, count: messages.length, messages });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── ALL AGENTS FEED ─────────────────────────────────────────────────────
// Papa view — everything all agents did recently, merged timeline.
app.get('/feed', (req, res) => {
  const windowMs = parseInt(req.query.window) || 60 * 60 * 1000;
  const limit    = parseInt(req.query.limit)  || 200;
  const requestedSurface = getRequestedSurface(req);
  try {
    const messages = db.getAllAgentsRecent(windowMs, limit, requestedSurface);
    res.json({
      ok: true,
      surface: requestedSurface || 'all',
      surfaceMode: requestedSurface ? 'scoped' : 'unscoped',
      count: messages.length,
      messages,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── SAVE SUMMARY ────────────────────────────────────────────────────────
// Called by the auto-summarizer or manually to store compressed context.
app.post('/summary', (req, res) => {
  const { agentId, periodStart, periodEnd, summaryText, sourceCount } = req.body;
  if (!agentId || !summaryText) {
    return res.status(400).json({ error: 'agentId, summaryText required' });
  }
  try {
    db.saveSummary({
      agentId,
      periodStart: periodStart || Date.now() - 3600000,
      periodEnd:   periodEnd   || Date.now(),
      summaryText,
      sourceCount: sourceCount || 0,
    });
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ─── GENERATE SESSION ID ──────────────────────────────────────────────────
// Helper — client can ask the server to generate a session ID
app.get('/session/new', (req, res) => {
  res.json({ sessionId: uuidv4(), surface: resolveSurface(req) });
});


// ─── PRUNE ───────────────────────────────────────────────────────────────
// Housekeeping — call via cron daily
app.post('/admin/prune', (req, res) => {
  const days = parseInt(req.body?.days) || 90;
  const deleted = db.pruneOld(days);
  res.json({ ok: true, deletedMessages: deleted });
});


// ─── HEALTH ───────────────────────────────────────────────────────────────
app.get('/health', (req, res) => {
  const agents = db.getAllAgents();
  res.json({
    ok: true,
    service: 'octa-context',
    surface: DEFAULT_SURFACE,
    uptime: process.uptime(),
    agentCount: agents.length,
    dbPath: db.dbPath,
    timestamp: new Date().toISOString(),
  });
});


// ─── START ────────────────────────────────────────────────────────────────
app.listen(PORT, HOST, () => {
  console.log(`[octa-context] Listening on ${HOST}:${PORT}`);
  console.log(`[octa-context] DB: ${db.dbPath}`);
  console.log(`[octa-context] Auth: ${AUTH_TOKEN ? 'enabled' : 'disabled (dev mode)'}`);
});

process.on('SIGTERM', () => { db.close(); process.exit(0); });
process.on('SIGINT',  () => { db.close(); process.exit(0); });
