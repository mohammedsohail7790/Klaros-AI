/**
 * The one place that stores the signed-in session.
 *
 * The session now survives closing the tab and restarting the browser (localStorage) — previously it lived in sessionStorage, so every new tab or browser
 * restart signed the person out. Sessions still end for real on the server side: signing out, resetting or changing the password revokes every token
 * (backend `token_version`), and a revoked token simply fails its next refresh.
 *
 * Reads fall back to sessionStorage so a session started before this change keeps working, and so storage that is blocked or unavailable never throws.
 */

const ACCESS = "klaros_access_token";
const REFRESH = "klaros_refresh_token";

function read(store: () => Storage, key: string): string | null {
  try {
    return store().getItem(key);
  } catch {
    return null;
  }
}

function write(store: () => Storage, key: string, value: string): boolean {
  try {
    store().setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function remove(store: () => Storage, key: string): void {
  try {
    store().removeItem(key);
  } catch {
    // storage unavailable: nothing to remove
  }
}

const local = () => window.localStorage;
const session = () => window.sessionStorage;

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return read(local, ACCESS) ?? read(session, ACCESS);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return read(local, REFRESH) ?? read(session, REFRESH);
}

export function setSession(tokens: { access_token: string; refresh_token: string }): void {
  if (typeof window === "undefined") return;
  const persisted = write(local, ACCESS, tokens.access_token) && write(local, REFRESH, tokens.refresh_token);
  if (persisted) {
    // one copy only, so a stale tab-scoped token can never shadow the current one
    remove(session, ACCESS);
    remove(session, REFRESH);
  } else {
    // localStorage is blocked (private mode, policy): fall back to this tab only
    write(session, ACCESS, tokens.access_token);
    write(session, REFRESH, tokens.refresh_token);
  }
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  for (const key of [ACCESS, REFRESH]) {
    remove(local, key);
    remove(session, key);
  }
}
