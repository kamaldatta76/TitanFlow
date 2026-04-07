"""
titan.gms_atlas.identity_resolution
Phase 1 identity resolution planner.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ResolutionPlan:
    comm_id: int
    resolved_cp_id: Optional[int]
    resolution_method: str
    confidence: float
    input_email: Optional[str]
    input_name: Optional[str]
    candidates_tried: int
    resolution_ms: int
    error: Optional[str] = None


def _decode(v) -> Optional[str]:
    """Decode bytes to str (handles SQL_ASCII postgres returning bytes)."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _extract_domain(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    m = re.search(r"@([\w.\-]+)$", email.strip().lower())
    return m.group(1) if m else None


def plan_resolution(comm_id: int, conn) -> ResolutionPlan:
    t0 = time.monotonic()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT sender_email_raw, sender_name_raw FROM gms_atlas.communications WHERE id = %s",
            (comm_id,),
        )
        row = cur.fetchone()

    if not row:
        return ResolutionPlan(
            comm_id=comm_id, resolved_cp_id=None, resolution_method="no_match",
            confidence=0.0, input_email=None, input_name=None, candidates_tried=0,
            resolution_ms=int((time.monotonic() - t0) * 1000),
            error=f"communications row {comm_id} not found",
        )

    sender_email = _decode(row[0])
    sender_name = _decode(row[1])
    email_lc = sender_email.strip().lower() if sender_email else None
    domain = _extract_domain(email_lc)
    candidates_tried = 0

    if email_lc:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM gms_atlas.counterparties WHERE LOWER(email_primary) = %s AND is_active = true LIMIT 1",
                (email_lc,),
            )
            candidates_tried += 1
            r = cur.fetchone()
        if r:
            return ResolutionPlan(comm_id=comm_id, resolved_cp_id=r[0], resolution_method="email_exact",
                confidence=1.0, input_email=email_lc, input_name=sender_name,
                candidates_tried=candidates_tried, resolution_ms=int((time.monotonic() - t0) * 1000))

    if email_lc:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.cp_id FROM gms_atlas.counterparty_email_aliases a JOIN gms_atlas.counterparties c ON c.id = a.cp_id WHERE LOWER(a.email) = %s AND c.is_active = true ORDER BY a.confidence DESC LIMIT 1",
                (email_lc,),
            )
            candidates_tried += 1
            r = cur.fetchone()
        if r:
            return ResolutionPlan(comm_id=comm_id, resolved_cp_id=r[0], resolution_method="email_alias",
                confidence=0.95, input_email=email_lc, input_name=sender_name,
                candidates_tried=candidates_tried, resolution_ms=int((time.monotonic() - t0) * 1000))

    if domain:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.id FROM gms_atlas.counterparties c JOIN gms_atlas.firms f ON f.id = c.firm_id WHERE LOWER(f.domain_primary) = %s AND c.is_active = true AND f.is_active = true ORDER BY c.id ASC LIMIT 1",
                (domain,),
            )
            candidates_tried += 1
            r = cur.fetchone()
        if r:
            return ResolutionPlan(comm_id=comm_id, resolved_cp_id=r[0], resolution_method="email_domain",
                confidence=0.7, input_email=email_lc, input_name=sender_name,
                candidates_tried=candidates_tried, resolution_ms=int((time.monotonic() - t0) * 1000))

    if sender_name:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM gms_atlas.counterparties WHERE display_name ILIKE %s AND is_active = true LIMIT 1",
                (f"%{sender_name.strip()}%",),
            )
            candidates_tried += 1
            r = cur.fetchone()
        if r:
            return ResolutionPlan(comm_id=comm_id, resolved_cp_id=r[0], resolution_method="name_match",
                confidence=0.6, input_email=email_lc, input_name=sender_name,
                candidates_tried=candidates_tried, resolution_ms=int((time.monotonic() - t0) * 1000))

    return ResolutionPlan(comm_id=comm_id, resolved_cp_id=None, resolution_method="no_match",
        confidence=0.0, input_email=email_lc, input_name=sender_name,
        candidates_tried=candidates_tried, resolution_ms=int((time.monotonic() - t0) * 1000))
