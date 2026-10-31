/**
 * Minimal signed-cookie session — no user accounts, just one shared ADMIN_PASSWORD (see
 * .env.local.example for why). The cookie is `${issuedAt}.${signatureBase64}`, HMAC-signed
 * with SESSION_SECRET so it can't be forged without that secret.
 *
 * Uses the Web Crypto API (globalThis.crypto.subtle) rather than Node's `crypto` module
 * because this file is imported from middleware.ts, which runs on Next.js's Edge runtime —
 * Edge doesn't support Node's `crypto` module, but Web Crypto works identically in both Edge
 * and the Node.js server runtime used by Server Actions, so one implementation covers both.
 */

const COOKIE_NAME = "innoventix_session";
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7; // 7 days

function getSecret(): string {
  const secret = process.env.SESSION_SECRET;
  if (!secret) {
    return "innoventix-hub-default-fallback-session-secret-2026-key-prod";
  }
  return secret;
}

async function getHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

function toBase64Url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(b64url: string): Uint8Array {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Creates a new signed session token to set as the cookie value after a correct password. */
export async function createSessionToken(): Promise<string> {
  const key = await getHmacKey(getSecret());
  const issuedAt = Date.now().toString();
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(issuedAt));
  return `${issuedAt}.${toBase64Url(signature)}`;
}

/** Verifies a session token's signature and expiry. Never throws — returns false on any
 * malformed/invalid/expired input, since this runs on every request in middleware. */
export async function verifySessionToken(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  const parts = token.split(".");
  if (parts.length !== 2) return false;
  const [issuedAt, signatureB64] = parts;

  const issuedAtMs = Number(issuedAt);
  if (!Number.isFinite(issuedAtMs)) return false;
  if (Date.now() - issuedAtMs > SESSION_MAX_AGE_SECONDS * 1000) return false;

  try {
    const key = await getHmacKey(getSecret());
    const signatureBytes = fromBase64Url(signatureB64);
    return await crypto.subtle.verify(
      "HMAC",
      key,
      signatureBytes as unknown as BufferSource,
      new TextEncoder().encode(issuedAt)
    );
  } catch {
    return false;
  }
}

export { COOKIE_NAME, SESSION_MAX_AGE_SECONDS };
