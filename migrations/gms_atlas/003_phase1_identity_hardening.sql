-- =============================================================================
-- 003_phase1_identity_hardening.sql
-- GMS Atlas Phase 1 — Identity Hardening
-- Schema: gms_atlas
-- Built: 2026-04-07
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. counterparty_email_aliases — additional emails known for a counterparty
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.counterparty_email_aliases (
    id              BIGSERIAL       PRIMARY KEY,
    cp_id           BIGINT          NOT NULL REFERENCES gms_atlas.counterparties(id) ON DELETE CASCADE,
    email           TEXT            NOT NULL,
    source          TEXT            NOT NULL DEFAULT 'manual'
                                   CHECK (source IN ('manual','auto_detected','import','merge')),
    confidence      NUMERIC(4,3)    NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (cp_id, email)
);

CREATE INDEX idx_gms_cp_alias_cp_id ON gms_atlas.counterparty_email_aliases (cp_id);
CREATE INDEX idx_gms_cp_alias_email ON gms_atlas.counterparty_email_aliases (email);

-- ---------------------------------------------------------------------------
-- 2. firm_history — tracks firm name changes / mergers
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.firm_history (
    id              BIGSERIAL       PRIMARY KEY,
    firm_id         BIGINT          NOT NULL REFERENCES gms_atlas.firms(id) ON DELETE CASCADE,
    previous_name   TEXT            NOT NULL,
    changed_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    changed_by      TEXT,
    notes           TEXT
);

CREATE INDEX idx_gms_firm_history_firm_id ON gms_atlas.firm_history (firm_id);

-- ---------------------------------------------------------------------------
-- 3. cp_merge_log — audit trail when two counterparties are merged
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.cp_merge_log (
    id              BIGSERIAL       PRIMARY KEY,
    canonical_cp_id BIGINT          NOT NULL REFERENCES gms_atlas.counterparties(id) ON DELETE RESTRICT,
    merged_cp_id    BIGINT          NOT NULL,
    merged_at       TIMESTAMPTZ     NOT NULL DEFAULT now(),
    merged_by       TEXT,
    notes           TEXT
);

CREATE INDEX idx_gms_cp_merge_canonical ON gms_atlas.cp_merge_log (canonical_cp_id);

-- ---------------------------------------------------------------------------
-- 4. vessel_name_refs — vessel name lookups associated with communications
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.vessel_name_refs (
    id              BIGSERIAL       PRIMARY KEY,
    comm_id         BIGINT          NOT NULL REFERENCES gms_atlas.communications(id) ON DELETE CASCADE,
    vessel_name     TEXT            NOT NULL,
    imo_number      TEXT,
    extracted_by    TEXT            NOT NULL DEFAULT 'manual'
                                   CHECK (extracted_by IN ('manual','regex','llm','import')),
    confidence      NUMERIC(4,3)    NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_gms_vessel_refs_comm_id ON gms_atlas.vessel_name_refs (comm_id);
CREATE INDEX idx_gms_vessel_refs_name ON gms_atlas.vessel_name_refs (vessel_name);
CREATE INDEX idx_gms_vessel_refs_imo ON gms_atlas.vessel_name_refs (imo_number)
    WHERE imo_number IS NOT NULL;

-- Grant
GRANT ALL ON ALL TABLES IN SCHEMA gms_atlas TO gms_atlas;
GRANT ALL ON ALL SEQUENCES IN SCHEMA gms_atlas TO gms_atlas;

COMMIT;
