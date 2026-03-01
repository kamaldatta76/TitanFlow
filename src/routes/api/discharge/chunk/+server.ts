import Database from 'better-sqlite3';
const db = new Database('pipeline.db');

export async function POST({ request }) {
  const { chunk_num, ai, clean_ratio, fixes_needed } = await request.json();

  if (ai !== 'Flow' && ai !== 'Ollie') {
    return new Response(JSON.stringify({ error: 'Invalid AI agent' }), { status: 400 });
  }

  const stmt = db.prepare('INSERT INTO pipeline_chunks (chunk_num, ai, clean_ratio, fixes_needed) VALUES (?, ?, ?, ?)');
  const info = stmt.run(chunk_num, ai, clean_ratio, fixes_needed);

  return new Response(JSON.stringify({ success: true, id: info.lastInsertRowid }));
}