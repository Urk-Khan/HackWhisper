-- =====================================================================
-- HackWhisper: AI Hacktoberfest Voice Calling Agent Database Schema
-- Compatible with PostgreSQL 14+ and Supabase
-- =====================================================================

-- 1. Main Events Table (Active and historical events with current version)
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    format TEXT,
    status TEXT,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    timezone TEXT,
    description TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    address_line1 TEXT,
    address_line2 TEXT,
    postal_code TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    website_url TEXT,
    registration_url TEXT,
    logo_url TEXT,
    raw_data JSONB,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Search and filter indexes
CREATE INDEX IF NOT EXISTS idx_events_city ON events (LOWER(city));
CREATE INDEX IF NOT EXISTS idx_events_name ON events (LOWER(name));
CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);
CREATE INDEX IF NOT EXISTS idx_events_starts_at ON events (starts_at);

-- 2. Historical Versioning Table (Preserves previous versions upon upsert updates)
CREATE TABLE IF NOT EXISTS event_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    snapshot JSONB NOT NULL,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_event_history_event_id ON event_history (event_id);

-- 3. Sync Logs (Audit trail of every 24-hour API fetch & upsert cycle)
CREATE TABLE IF NOT EXISTS event_sync_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    total_fetched INTEGER DEFAULT 0,
    new_events_count INTEGER DEFAULT 0,
    updated_events_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'success',
    error_message TEXT
);

-- 4. Event Notifications (Records registration links & details sent via Email / SMS)
CREATE TABLE IF NOT EXISTS event_notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id TEXT,
    recipient TEXT NOT NULL,
    channel TEXT NOT NULL, -- 'email' or 'sms'
    message_content TEXT,
    status TEXT DEFAULT 'sent',
    call_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON event_notifications (recipient);

-- 5. Call Logs (Telephony conversation logs, duration, transcript & outcome)
CREATE TABLE IF NOT EXISTS call_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id TEXT UNIQUE,
    caller_phone TEXT,
    duration_seconds INTEGER,
    transcript TEXT,
    outcome TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_logs_phone ON call_logs (caller_phone);

