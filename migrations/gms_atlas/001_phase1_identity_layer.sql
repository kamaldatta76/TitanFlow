-- =============================================================================
-- 001_phase1_identity_layer.sql
-- GMS Atlas Phase 1 — Identity Layer
-- Schema: gms_atlas
-- Built: 2026-04-07
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS gms_atlas;

-- ---------------------------------------------------------------------------
-- 1. orgs — GMS internal organisations (GMS Command and its entities)
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.orgs (
    id          BIGSERIAL       PRIMARY KEY,
    name        TEXT            NOT NULL,
    slug        TEXT            NOT NULL UNIQUE,
    is_active   BOOLEAN         NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_gms_orgs_slug ON gms_atlas.orgs (slug);
CREATE INDEX idx_gms_orgs_active ON gms_atlas.orgs (is_active);

-- ---------------------------------------------------------------------------
-- 2. firms — external shipping firms / counterparty companies
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.firms (
    id              BIGSERIAL       PRIMARY KEY,
    name            TEXT            NOT NULL,
    slug            TEXT            NOT NULL UNIQUE,
    country_code    TEXT,
    firm_type       TEXT            NOT NULL DEFAULT 'unknown'
                                   CHECK (firm_type IN ('owner','charterer','broker','bank','shipper','port_agent','surveyor','unknown')),
    domain_primary  TEXT,
    domains_other   JSONB           NOT NULL DEFAULT '[]',
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_gms_firms_slug ON gms_atlas.firms (slug);
CREATE INDEX idx_gms_firms_name ON gms_atlas.firms (name);
CREATE INDEX idx_gms_firms_domain ON gms_atlas.firms (domain_primary);
CREATE INDEX idx_gms_firms_type ON gms_atlas.firms (firm_type);
CREATE INDEX idx_gms_firms_active ON gms_atlas.firms (is_active);
CREATE INDEX idx_gms_firms_domains_other ON gms_atlas.firms USING gin (domains_other);

-- ---------------------------------------------------------------------------
-- 3. counterparties — individual contacts at external firms
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.counterparties (
    id              BIGSERIAL       PRIMARY KEY,
    firm_id         BIGINT          REFERENCES gms_atlas.firms(id) ON DELETE SET NULL,
    display_name    TEXT            NOT NULL,
    email_primary   TEXT,
    emails_other    JSONB           NOT NULL DEFAULT '[]',
    phone           TEXT,
    job_title       TEXT,
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_gms_cp_firm_id ON gms_atlas.counterparties (firm_id);
CREATE INDEX idx_gms_cp_email ON gms_atlas.counterparties (email_primary);
CREATE INDEX idx_gms_cp_name ON gms_atlas.counterparties (display_name);
CREATE INDEX idx_gms_cp_active ON gms_atlas.counterparties (is_active);
CREATE INDEX idx_gms_cp_emails_other ON gms_atlas.counterparties USING gin (emails_other);

-- ---------------------------------------------------------------------------
-- 4. channels — inbound/outbound mailboxes and other communication channels
-- ---------------------------------------------------------------------------
CREATE TABLE gms_atlas.channels (
    id              BIGSERIAL       PRIMARY KEY,
    org_id          BIGINT          NOT NULL REFERENCES gms_atlas.orgs(id) ON DELETE RESTRICT,
    channel_type    TEXT            NOT NULL DEFAULT 'email'
                                   CHECK (channel_type IN ('email','whatsapp','sms','voice','manual')),
    address         TEXT            NOT NULL,
    display_name    TEXT,
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (org_id, address)
);

CREATE INDEX idx_gms_channels_org_id ON gms_atlas.channels (org_id);
CREATE INDEX idx_gms_channels_type ON gms_atlas.channels (channel_type);
CREATE INDEX idx_gms_channels_address ON gms_atlas.channels (address);
CREATE INDEX idx_gms_channels_active ON gms_atlas.channels (is_active);

-- Grant schema usage to gms_atlas role
GRANT USAGE ON SCHEMA gms_atlas TO gms_atlas;
GRANT ALL ON ALL TABLES IN SCHEMA gms_atlas TO gms_atlas;
GRANT ALL ON ALL SEQUENCES IN SCHEMA gms_atlas TO gms_atlas;
ALTER DEFAULT PRIVILEGES IN SCHEMA gms_atlas GRANT ALL ON TABLES TO gms_atlas;
ALTER DEFAULT PRIVILEGES IN SCHEMA gms_atlas GRANT ALL ON SEQUENCES TO gms_atlas;

COMMIT;
