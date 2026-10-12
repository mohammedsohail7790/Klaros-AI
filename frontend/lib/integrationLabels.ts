/**
 * Customer-facing wording for the Integrations screen. The backend reports closed status codes and developer diagnostics ("SERVICETITAN_CLIENT_ID not configured",
 * "verified via GET /v1/balance"); an owner should read none of that. The real status still decides the colour and what is offered: only the words change.
 */

const STATUS_LABEL: Record<string, string> = {
  CONNECTED: "Connected",
  NOT_CONNECTED: "Not connected",
  ERROR: "Needs attention",
  NOT_IMPLEMENTED: "Coming soon",
};

export const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

/** Turns a provider diagnostic into a sentence an owner can act on, without the environment-variable or API-route names. */
export function plainDetail(status: string, detail: string | null | undefined): string {
  const d = (detail ?? "").trim();
  if (status === "CONNECTED") return "Connected and working.";
  if (status === "NOT_IMPLEMENTED") return "Not available yet.";
  if (/connect per-tenant|oauth app configured/i.test(d)) return "Ready: connect your own account below.";
  if (/no .*integration configured|not configured|oauth not configured/i.test(d)) return "Not set up yet.";
  if (status === "ERROR") return "Klaros could not use the saved connection. Reconnect it, or check it with the provider.";
  return d && !/[A-Z]{3,}_[A-Z_]+/.test(d) && !/GET \/|POST \//.test(d) ? d : "Not connected.";
}

/** The Integrations page lists many tools; a Medical Tourism owner needs the ones that touch patients, money and communication. */
export const FIELD_SERVICE_ONLY = new Set(["servicetitan", "jobber", "supplier", "supplier_procurement", "google_ads", "meta_ads"]);
