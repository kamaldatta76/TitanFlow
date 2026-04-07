-- =============================================================================
-- 002_phase1_communications_ledger.sql
-- GMS Atlas Phase 1 — Communications Ledger (append-only)
-- Schema: gms_atlas
-- Built: 2026-04-07
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. communications — append-only ledger of inbound/outbound comms
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.communications (
    id                      BIGSERIAL       PRIMARY KEY,
    org_id                  BIGINT          NOT NULL REFERENCES gms_atlas.orgs(id) ON DELETE RESTRICT,
    channel_id              BIGINT          REFERENCES gms_atlas.channels(id) ON DELETE SET NULL,
    source_system           TEXT            NOT NULL DEFAULT 'manual'
                                           CHECK (source_system IN ('email','whatsapp','sms','voice','manual','import','test')),
    doc_no                  TEXT,
    direction               TEXT            NOT NULL
                                           CHECK (direction IN ('inbound','outbound','internal')),
    occurred_at             TIMESTAMPTZ     NOT NULL DEFAULT now(),
    subject                 TEXT,
    body_snippet            TEXT,
    raw_payload             JSONB           NOT NULL DEFAULT '{}',
    -- identity resolution fields (populated by worker)
    primary_sender_cp_id    BIGINT          REFERENCES gms_atlas.counterparties(id) ON DELETE SET NULL,
    sender_email_raw        TEXT,
    sender_name_raw         TEXT,
    resolution_status       TEXT            NOT NULL DEFAULT 'unresolved'
                                           CHECK (resolution_status IN ('unresolved','resolved','manual','skipped')),
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    -- doc_no uniqueness is per-org, per-source-system (partial unique)
    CONSTRAINT uq_comm_doc_no UNIQUE NULLS NOT DISTINCT (org_id, source_system, doc_no)
);

CREATE INDEX idx_gms_comm_org_id ON gms_atlas.communications (org_id);
CREATE INDEX idx_gms_comm_channel_id ON gms_atlas.communications (channel_id);
CREATE INDEX idx_gms_comm_occurred_at ON gms_atlas.communications (occurred_at DESC);
CREATE INDEX idx_gms_comm_direction ON gms_atlas.communications (direction);
CREATE INDEX idx_gms_comm_source_system ON gms_atlas.communications (source_system);
CREATE INDEX idx_gms_comm_resolution_status ON gms_atlas.communications (resolution_status)
    WHERE resolution_status = 'unresolved';
CREATE INDEX idx_gms_comm_primary_sender ON gms_atlas.communications (primary_sender_cp_id)
    WHERE primary_sender_cp_id IS NOT NULL;
CREATE INDEX idx_gms_comm_sender_email ON gms_atlas.communications (sender_email_raw)
    WHERE sender_email_raw IS NOT NULL;

-- Grant
GRANT ALL ON gms_atlas.communications TO gms_atlas;
GRANT ALL ON SEQUENCE gms_atlas.communications_id_seq TO gms_atlas;

COMMIT;
