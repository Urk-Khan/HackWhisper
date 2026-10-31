-- Innoventix Hub voice agent — Supabase schema.
-- Run this once in your Supabase project's SQL editor.

-- Pre-populated externally when a deal closes (this bot only READS this table, via
-- booking_db.get_customer_by_phone, to know purchase history for the cross-sell scenario).
-- "phone" should be in the same format your Telnyx calls report as the From number
-- (typically E.164, e.g. "+442012345678") — get_customer_by_phone does an exact match.
create table customers (
  id bigint primary key generated always as identity,
  phone text unique not null,
  name text,
  email text,
  purchased_services text,  -- free text, e.g. "AI Automation" or "AI Automation, Web Development"
  created_at timestamptz default now()
);

-- Written by mark_potential_lead (tools.py) whenever a caller shows real interest but
-- doesn't commit to booking. "status" starts as "warm" — update it manually as the sales
-- team works the lead (e.g. "contacted", "converted", "cold").
create table leads (
  id bigint primary key generated always as identity,
  name text,
  phone text,
  interested_service text,
  reason text,
  status text default 'warm',
  call_id text,
  created_at timestamptz default now()
);

-- Audit-trail log of every successfully booked meeting (both sales and support). Google
-- Sheets + Google Calendar remain the operational source of truth for actual scheduling —
-- this table is for reporting/history only, written after both of those succeed.
create table meetings (
  id bigint primary key generated always as identity,
  meeting_type text,        -- "sales" or "support"
  customer_name text,
  phone text,
  email text,
  topic text,
  date text,
  time text,
  meet_link text,
  call_id text,
  created_at timestamptz default now()
);

-- Call log: written at the end of every call (success or failure). Contains a clean
-- Caller/Agent transcript, auto-classified outcome, and duration. Written
-- fire-and-forget by booking_db.save_call_record() — a failure here never affects the call.
create table if not exists call_logs (
  id bigint primary key generated always as identity,
  call_id text,
  caller_phone text,
  duration_seconds integer,
  transcript text,
  outcome text,  -- "meeting_booked" | "warm_lead" | "info_inquiry" | "transferred" | "dropped_call"
  created_at timestamptz default now()
);
