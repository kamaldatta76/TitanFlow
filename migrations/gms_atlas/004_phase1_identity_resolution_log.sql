-- =============================================================================
-- 004_phase1_identity_resolution_log.sql
-- GMS Atlas Phase 1 — Identity Resolution Log
-- Schema: gms_atlas
-- Built: 2026-04-07
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. identity_resolution_log — audit trail for every resolution attempt
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.identity_resolution_log (
    id                  BIGSERIAL       PRIMARY KEY,
    comm_id             BIGINT          NOT NULL REFERENCES gms_atlas.communications(id) ON DELETE CASCADE,
    resolved_cp_id      BIGINT          REFERENCES gms_atlas.counterparties(id) ON DELETE SET NULL,
    resolution_method   TEXT            NOT NULL
                                       CHECK (resolution_method IN (
                                           'email_exact','email_alias','email_domain',
                                           'name_match','manual','import','no_match'
                                       )),
    confidence          NUMERIC(4,3)    NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0 AND 1),
    input_email         TEXT,
    input_name          TEXT,
    candidates_tried    INT             NOT NULL DEFAULT 0,
    resolution_ms       INT,
    worker_version      TEXT            NOT NULL DEFAULT '1.0',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_gms_irl_comm_id ON gms_atlas.identity_resolution_log (comm_id);
CREATE INDEX idx_gms_irl_resolved_cp ON gms_atlas.identity_resolution_log (resolved_cp_id)
    WHERE resolved_cp_id IS NOT NULL;
CREATE INDEX idx_gms_irl_method ON gms_atlas.identity_resolution_log (resolution_method);
CREATE INDEX idx_gms_irl_created_at ON gms_atlas.identity_resolution_log (created_at DESC);

-- Grant
GRANT ALL ON gms_atlas.identity_resolution_log TO gms_atlas;
GRANT ALL ON SEQUENCE gms_atlas.identity_resolution_log_id_seq TO gms_atlas;

COMMIT;
