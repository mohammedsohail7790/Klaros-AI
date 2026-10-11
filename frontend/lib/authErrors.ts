import { ApiError } from "@/lib/api";

/**
 * Turns whatever the API or the network produced into one plain sentence a person can act on. The backend's validators speak in developer terms
 * ("value is not a valid email address: The part after the @-sign is a special-use or reserved name..."); none of that should reach the screen.
 */
export function friendlyAuthError(err: unknown, fallback = "Something went wrong. Please try again."): string {
  if (err instanceof ApiError) {
    const msg = err.message.toLowerCase();
    if (msg.includes("not a valid email") || msg.includes("email address")) return "Enter a valid email address, for example name@company.com.";
    if (err.status === 429) return "Too many attempts. Wait a minute and try again.";
    if (err.status >= 500) return "Klaros is having trouble right now. Please try again in a moment.";
    if (err.status === 401) return "That email and password don't match. Check them and try again.";
    return err.message || fallback;
  }
  if (err instanceof TypeError) return "We couldn't reach Klaros. Check your connection and try again.";
  return fallback;
}

/** Where to go after signing in. Only same-site paths are honoured, so a crafted link cannot send someone to another site. */
export function safeNextPath(next: string | null | undefined, fallback = "/business"): string {
  if (!next) return fallback;
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  if (next.startsWith("/login") || next.startsWith("/register")) return fallback;
  return next;
}

/** A short, honest strength hint for the new-password field. It guides; the server enforces the real minimum. */
export function passwordHint(password: string): { ok: boolean; text: string } {
  if (password.length === 0) return { ok: false, text: "At least 8 characters." };
  if (password.length < 8) return { ok: false, text: `${8 - password.length} more character${8 - password.length === 1 ? "" : "s"} needed.` };
  if (/^(.)\1+$/.test(password) || /^(password|12345678|qwertyui)/i.test(password)) return { ok: false, text: "That is too easy to guess. Try something longer or less common." };
  return { ok: true, text: "Looks good." };
}
