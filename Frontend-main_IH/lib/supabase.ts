/**
 * Database client for Innoventix Hub Dashboard.
 * Supports direct PostgreSQL queries via pg Pool (using DATABASE_URL or POSTGRES_URL),
 * with backward-compatible fallback to Supabase REST if configured.
 *
 * This file must only ever be imported from Server Components, Server Actions,
 * or Route Handlers — never from a "use client" file.
 */

import { Pool, QueryResult } from "pg";

let pgPool: Pool | null = null;
let schemaInitialized = false;

const INIT_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS customers (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  phone TEXT UNIQUE NOT NULL,
  name TEXT,
  email TEXT,
  purchased_services TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name TEXT,
  phone TEXT,
  interested_service TEXT,
  reason TEXT,
  status TEXT DEFAULT 'warm',
  call_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meetings (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  meeting_type TEXT,
  customer_name TEXT,
  phone TEXT,
  email TEXT,
  topic TEXT,
  date TEXT,
  time TEXT,
  meet_link TEXT,
  call_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS call_logs (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  call_id TEXT,
  caller_phone TEXT,
  duration_seconds INTEGER,
  transcript TEXT,
  outcome TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
`;

function getDatabaseUrl(): string | null {
  const url = process.env.DATABASE_URL || process.env.POSTGRES_URL || process.env.POSTGRESQL_URL;
  return url ? url.trim() : null;
}

function getPool(): Pool | null {
  const connectionString = getDatabaseUrl();
  if (!connectionString) {
    return null;
  }
  if (!pgPool) {
    pgPool = new Pool({
      connectionString,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
      ssl: connectionString.includes("sslmode=require") ? { rejectUnauthorized: false } : undefined,
    });
  }
  return pgPool;
}

async function ensureSchema(pool: Pool) {
  if (schemaInitialized) return;
  try {
    await pool.query(INIT_SCHEMA_SQL);
    schemaInitialized = true;
  } catch (err) {
    console.warn("Could not auto-create schema:", err);
  }
}

// Supabase REST fallback helpers
function getSupabaseConfig() {
  const url = process.env.SUPABASE_URL?.replace(/\/$/, "");
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) {
    return null;
  }
  return { url, key };
}

function supabaseHeaders(key: string, extra?: Record<string, string>) {
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
    ...extra,
  };
}

/** Sanitize table names to protect against SQL injection */
function sanitizeTable(table: string): string {
  const allowed = ["customers", "leads", "meetings", "call_logs"];
  const clean = table.trim().toLowerCase();
  if (!allowed.includes(clean)) {
    throw new Error(`Unauthorized table access: ${table}`);
  }
  return clean;
}

/** GET rows from a table with a PostgREST query string or direct PostgreSQL query */
export async function fetchRows<T>(table: string, query: string): Promise<T[]> {
  const tableName = sanitizeTable(table);
  const pool = getPool();

  if (pool) {
    await ensureSchema(pool);

    // Parse query params like "select=*&order=created_at.desc&limit=300"
    let orderBy = "created_at DESC";
    let limit = 200;

    const parts = query.split("&");
    for (const part of parts) {
      const [k, v] = part.split("=");
      if (k === "order" && v) {
        if (v.includes("desc")) {
          const col = v.replace(".desc", "").trim();
          orderBy = `${col} DESC`;
        } else if (v.includes("asc")) {
          const col = v.replace(".asc", "").trim();
          orderBy = `${col} ASC`;
        }
      } else if (k === "limit" && v) {
        const parsedLimit = parseInt(v, 10);
        if (!isNaN(parsedLimit) && parsedLimit > 0) {
          limit = parsedLimit;
        }
      }
    }

    const sql = `SELECT * FROM ${tableName} ORDER BY ${orderBy} LIMIT ${limit};`;
    const result: QueryResult = await pool.query(sql);

    // Normalize dates to ISO strings to maintain exact interface compatibility with Supabase
    return result.rows.map((row) => {
      const formatted = { ...row };
      if (formatted.created_at instanceof Date) {
        formatted.created_at = formatted.created_at.toISOString();
      }
      return formatted as T;
    });
  }

  // Fallback to Supabase REST
  const supabase = getSupabaseConfig();
  if (supabase) {
    const res = await fetch(`${supabase.url}/rest/v1/${tableName}?${query}`, {
      headers: supabaseHeaders(supabase.key),
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`Supabase fetch failed for ${tableName} (${res.status}): ${await res.text()}`);
    }
    return res.json();
  }

  throw new Error("Neither DATABASE_URL nor SUPABASE_URL configured in environment variables");
}

/** PATCH a single row by id (e.g. updating a lead's status) */
export async function patchRow(
  table: string,
  id: number | string,
  body: Record<string, unknown>
): Promise<void> {
  const tableName = sanitizeTable(table);
  const pool = getPool();

  if (pool) {
    await ensureSchema(pool);

    const keys = Object.keys(body);
    if (keys.length === 0) return;

    const setClauses: string[] = [];
    const values: unknown[] = [];

    keys.forEach((key, idx) => {
      // Validate column names
      const cleanCol = key.replace(/[^a-zA-Z0-9_]/g, "");
      setClauses.push(`${cleanCol} = $${idx + 1}`);
      values.push(body[key]);
    });

    values.push(id);
    const sql = `UPDATE ${tableName} SET ${setClauses.join(", ")} WHERE id = $${values.length};`;
    await pool.query(sql, values);
    return;
  }

  // Fallback to Supabase REST
  const supabase = getSupabaseConfig();
  if (supabase) {
    const res = await fetch(`${supabase.url}/rest/v1/${tableName}?id=eq.${id}`, {
      method: "PATCH",
      headers: supabaseHeaders(supabase.key, { Prefer: "return=minimal" }),
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!res.ok) {
      throw new Error(`Supabase patch failed for ${tableName}#${id} (${res.status}): ${await res.text()}`);
    }
    return;
  }

  throw new Error("Neither DATABASE_URL nor SUPABASE_URL configured in environment variables");
}
