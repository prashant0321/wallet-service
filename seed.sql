-- =============================================================================
-- Wallet Service — Database Schema & Seed Data
-- =============================================================================
-- Run this script against a blank PostgreSQL database:
--   psql -U wallet_user -d wallet_db -f seed.sql
--
-- Or via Docker (after `docker compose up -d db`):
--   docker exec -i wallet_db psql -U wallet_user -d wallet_db < seed.sql
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- 1. Asset Types
CREATE TABLE IF NOT EXISTS asset_types (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL UNIQUE,
    symbol      VARCHAR(20)  NOT NULL UNIQUE,
    description TEXT,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 2. Accounts  (users + system accounts)
CREATE TABLE IF NOT EXISTS accounts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username   VARCHAR(150) NOT NULL UNIQUE,
    email      VARCHAR(255) UNIQUE,
    is_system  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 3. Wallets  (one per account × asset_type)
CREATE TABLE IF NOT EXISTS wallets (
    id            UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    UUID    NOT NULL REFERENCES accounts(id),
    asset_type_id UUID    NOT NULL REFERENCES asset_types(id),
    balance       NUMERIC(20, 4) NOT NULL DEFAULT 0
                      CONSTRAINT ck_wallet_balance_non_negative CHECK (balance >= 0),
    version       INTEGER      NOT NULL DEFAULT 0,   -- optimistic lock counter
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wallet_account_asset UNIQUE (account_id, asset_type_id)
);
CREATE INDEX IF NOT EXISTS ix_wallet_account_asset ON wallets(account_id, asset_type_id);

-- 4. Transactions  (append-only double-entry ledger)
CREATE TABLE IF NOT EXISTS transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_id     UUID         NOT NULL,           -- groups debit+credit entries
    transaction_type VARCHAR(20)  NOT NULL,
    wallet_id        UUID         NOT NULL REFERENCES wallets(id),
    amount           NUMERIC(20, 4) NOT NULL,         -- positive=credit, negative=debit
    balance_after    NUMERIC(20, 4) NOT NULL,
    description      TEXT,
    idempotency_key  VARCHAR(255),
    metadata         TEXT,                            -- JSON blob for extra context
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_transaction_wallet_created ON transactions(wallet_id, created_at);
CREATE INDEX IF NOT EXISTS ix_transaction_reference      ON transactions(reference_id);
CREATE INDEX IF NOT EXISTS ix_transaction_idempotency    ON transactions(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_transaction_type           ON transactions(transaction_type);

-- 5. Idempotency Keys  (prevents double-processing of retried requests)
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key           VARCHAR(255) NOT NULL UNIQUE,
    endpoint      VARCHAR(100) NOT NULL,
    response_body TEXT         NOT NULL,   -- JSON-serialised response
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_idempotency_key ON idempotency_keys(key);

-- ---------------------------------------------------------------------------
-- Seed Data
-- ---------------------------------------------------------------------------

-- ── 1. Asset Types ──────────────────────────────────────────────────────────
INSERT INTO asset_types (id, name, symbol, description, is_active) VALUES
    ('a1000000-0000-0000-0000-000000000001', 'Gold Coins',      'GC',  'Primary in-game currency for purchases and upgrades',  TRUE),
    ('a1000000-0000-0000-0000-000000000002', 'Diamonds',        'DIA', 'Premium hard currency, obtained via purchase or events', TRUE),
    ('a1000000-0000-0000-0000-000000000003', 'Loyalty Points',  'LP',  'Earned by daily logins and completing missions',         TRUE)
ON CONFLICT (id) DO NOTHING;

-- ── 2. System Accounts ──────────────────────────────────────────────────────
--   Treasury  : source wallet for top-ups (backed by real payments)
--   Bonus Pool: source wallet for free credit grants
--   Revenue   : destination wallet when users spend credits
INSERT INTO accounts (id, username, email, is_system, is_active) VALUES
    ('b1000000-0000-0000-0000-000000000001', 'system_treasury',   NULL, TRUE, TRUE),
    ('b1000000-0000-0000-0000-000000000002', 'system_bonus_pool', NULL, TRUE, TRUE),
    ('b1000000-0000-0000-0000-000000000003', 'system_revenue',    NULL, TRUE, TRUE)
ON CONFLICT (id) DO NOTHING;

-- System wallets — very high starting balance to act as an infinite source
INSERT INTO wallets (id, account_id, asset_type_id, balance) VALUES
    -- Treasury: Gold Coins
    ('w1000000-0000-0000-0000-000000000001',
     'b1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000001', 99999999.0000),
    -- Treasury: Diamonds
    ('w1000000-0000-0000-0000-000000000002',
     'b1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000002', 99999999.0000),
    -- Treasury: Loyalty Points
    ('w1000000-0000-0000-0000-000000000003',
     'b1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000003', 99999999.0000),
    -- Bonus Pool: Gold Coins
    ('w1000000-0000-0000-0000-000000000004',
     'b1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000001', 99999999.0000),
    -- Bonus Pool: Diamonds
    ('w1000000-0000-0000-0000-000000000005',
     'b1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000002', 99999999.0000),
    -- Bonus Pool: Loyalty Points
    ('w1000000-0000-0000-0000-000000000006',
     'b1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000003', 99999999.0000),
    -- Revenue: Gold Coins
    ('w1000000-0000-0000-0000-000000000007',
     'b1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000001', 0.0000),
    -- Revenue: Diamonds
    ('w1000000-0000-0000-0000-000000000008',
     'b1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000002', 0.0000),
    -- Revenue: Loyalty Points
    ('w1000000-0000-0000-0000-000000000009',
     'b1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000003', 0.0000)
ON CONFLICT (id) DO NOTHING;

-- ── 3. User Accounts ────────────────────────────────────────────────────────
INSERT INTO accounts (id, username, email, is_system, is_active) VALUES
    ('c1000000-0000-0000-0000-000000000001', 'alice',   'alice@example.com', FALSE, TRUE),
    ('c1000000-0000-0000-0000-000000000002', 'bob',     'bob@example.com',   FALSE, TRUE),
    ('c1000000-0000-0000-0000-000000000003', 'charlie', 'charlie@example.com', FALSE, TRUE)
ON CONFLICT (id) DO NOTHING;

-- User wallets with initial balances
INSERT INTO wallets (id, account_id, asset_type_id, balance) VALUES
    -- Alice: 500 Gold Coins, 50 Diamonds, 200 Loyalty Points
    ('w2000000-0000-0000-0000-000000000001',
     'c1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000001', 500.0000),
    ('w2000000-0000-0000-0000-000000000002',
     'c1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000002', 50.0000),
    ('w2000000-0000-0000-0000-000000000003',
     'c1000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000003', 200.0000),
    -- Bob: 1000 Gold Coins, 0 Diamonds, 350 Loyalty Points
    ('w2000000-0000-0000-0000-000000000004',
     'c1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000001', 1000.0000),
    ('w2000000-0000-0000-0000-000000000005',
     'c1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000002', 0.0000),
    ('w2000000-0000-0000-0000-000000000006',
     'c1000000-0000-0000-0000-000000000002',
     'a1000000-0000-0000-0000-000000000003', 350.0000),
    -- Charlie: 250 Gold Coins, 10 Diamonds, 0 Loyalty Points
    ('w2000000-0000-0000-0000-000000000007',
     'c1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000001', 250.0000),
    ('w2000000-0000-0000-0000-000000000008',
     'c1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000002', 10.0000),
    ('w2000000-0000-0000-0000-000000000009',
     'c1000000-0000-0000-0000-000000000003',
     'a1000000-0000-0000-0000-000000000003', 0.0000)
ON CONFLICT (id) DO NOTHING;

-- Record the initial credits as TOPUP transactions so the ledger is complete
DO $$
DECLARE
    ref_id UUID := gen_random_uuid();
BEGIN
    -- Alice initial balances
    INSERT INTO transactions (reference_id, transaction_type, wallet_id, amount, balance_after, description)
    VALUES
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000001', 500.0000,  500.0000,  'Initial seed balance — Gold Coins'),
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000002',  50.0000,   50.0000,  'Initial seed balance — Diamonds'),
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000003', 200.0000,  200.0000,  'Initial seed balance — Loyalty Points');
END $$;

DO $$
DECLARE
    ref_id UUID := gen_random_uuid();
BEGIN
    -- Bob initial balances
    INSERT INTO transactions (reference_id, transaction_type, wallet_id, amount, balance_after, description)
    VALUES
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000004', 1000.0000, 1000.0000, 'Initial seed balance — Gold Coins'),
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000006',  350.0000,  350.0000, 'Initial seed balance — Loyalty Points');
END $$;

DO $$
DECLARE
    ref_id UUID := gen_random_uuid();
BEGIN
    -- Charlie initial balances
    INSERT INTO transactions (reference_id, transaction_type, wallet_id, amount, balance_after, description)
    VALUES
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000007', 250.0000,  250.0000,  'Initial seed balance — Gold Coins'),
        (ref_id, 'TOPUP', 'w2000000-0000-0000-0000-000000000008',  10.0000,   10.0000,  'Initial seed balance — Diamonds');
END $$;

-- ---------------------------------------------------------------------------
-- Verify seed
-- ---------------------------------------------------------------------------
SELECT
    a.username,
    at.symbol,
    w.balance
FROM wallets w
JOIN accounts    a  ON a.id = w.account_id
JOIN asset_types at ON at.id = w.asset_type_id
ORDER BY a.username, at.symbol;
