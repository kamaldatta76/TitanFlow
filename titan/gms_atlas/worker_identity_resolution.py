"""
titan.gms_atlas.worker_identity_resolution
==========================================
Phase 1 identity resolution worker.

Usage:
  python3 -m titan.gms_atlas.worker_identity_resolution <comm_id>
  python3 -m titan.gms_atlas.worker_identity_resolution --batch [--limit N]
"""
from __future__ import annotations

import os
import sys
import argparse
import psycopg

from titan.gms_atlas.identity_resolution import plan_resolution


DB_URL = os.environ.get("GMS_ATLAS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def get_conn():
    if not DB_URL:
        raise RuntimeError("GMS_ATLAS_DATABASE_URL not set")
    return psycopg.connect(DB_URL, autocommit=False)


def resolve(comm_id: int) -> dict:
    with get_conn() as conn:
        plan = plan_resolution(comm_id, conn)
        if plan.error:
            print(f"[ERROR] {plan.error}", file=sys.stderr)
            return {"ok": False, "error": plan.error}

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gms_atlas.identity_resolution_log
                  (comm_id, resolved_cp_id, resolution_method, confidence,
                   input_email, input_name, candidates_tried, resolution_ms, worker_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '1.0')
                """,
                (
                    plan.comm_id, plan.resolved_cp_id, plan.resolution_method,
                    plan.confidence, plan.input_email, plan.input_name,
                    plan.candidates_tried, plan.resolution_ms,
                ),
            )

            if plan.resolved_cp_id is not None:
                cur.execute(
                    """
                    UPDATE gms_atlas.communications
                    SET primary_sender_cp_id = %s,
                        resolution_status = 'resolved'
                    WHERE id = %s
                    """,
                    (plan.resolved_cp_id, plan.comm_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE gms_atlas.communications
                    SET resolution_status = 'skipped'
                    WHERE id = %s AND resolution_status = 'unresolved'
                    """,
                    (plan.comm_id,),
                )

        conn.commit()

    result = {
        "ok": True,
        "comm_id": plan.comm_id,
        "resolved_cp_id": plan.resolved_cp_id,
        "method": plan.resolution_method,
        "confidence": float(plan.confidence),
        "resolution_ms": plan.resolution_ms,
        "candidates_tried": plan.candidates_tried,
    }
    print(f"[RESOLVE] comm_id={comm_id} method={plan.resolution_method} cp={plan.resolved_cp_id} conf={plan.confidence:.2f} ms={plan.resolution_ms}")
    return result


def batch_resolve(limit: int = 100) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM gms_atlas.communications
                WHERE resolution_status = 'unresolved'
                ORDER BY occurred_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            ids = [r[0] for r in cur.fetchall()]

    print(f"[BATCH] {len(ids)} unresolved communications to process")
    ok = 0
    skip = 0
    for comm_id in ids:
        result = resolve(comm_id)
        if result.get("resolved_cp_id"):
            ok += 1
        else:
            skip += 1
    print(f"[BATCH DONE] resolved={ok} skipped={skip} total={len(ids)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GMS Atlas Phase 1 Identity Resolution Worker")
    parser.add_argument("comm_id", nargs="?", type=int, help="Single communication ID to resolve")
    parser.add_argument("--batch", action="store_true", help="Process all unresolved communications")
    parser.add_argument("--limit", type=int, default=100, help="Batch limit (default 100)")
    args = parser.parse_args()

    if args.batch:
        batch_resolve(limit=args.limit)
    elif args.comm_id:
        result = resolve(args.comm_id)
        sys.exit(0 if result["ok"] else 1)
    else:
        parser.print_help()
        sys.exit(1)
