/** Plain-language names for the engine's internal identifiers, so no screen shows
 * `notifications.create_notification` or `NEW` to a business owner. Unknown values fall
 * back to a readable form of the identifier itself — never to "null"/"undefined". */

const ACTIONS: Record<string, string> = {
  "notifications.create_notification": "Notify the team",
  "crm.update_lead": "Update the lead",
  "crm.create_note": "Add a note",
  "crm.qualify_lead": "Record qualification",
  "crm.ai_qualify_lead_advisory": "Suggest a qualification (AI)",
  "crm.create_appointment": "Book an appointment",
  "crm.search_leads": "Look up leads",
  "crm.get_lead": "Read a lead",
};

const TRIGGERS: Record<string, string> = {
  "lead.created": "A new lead arrives",
  "halla.lead.escalated": "The AI workforce hands a lead to a person",
  "halla.lead.qualified": "The AI workforce qualifies a lead",
  "quote.expired": "A quote expires",
  "exception.created": "An exception is raised",
  "invoice.overdue": "An invoice becomes overdue",
};

export function titleCase(s: string): string {
  const t = s.replace(/[._]+/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : "";
}

export function actionLabel(tool: string): string {
  return ACTIONS[tool] ?? titleCase(tool.split(".").slice(-1)[0] ?? tool);
}

export function triggerLabel(event: string | null | undefined): string {
  if (!event) return "Started manually";
  return TRIGGERS[event] ?? `When “${titleCase(event)}” happens`;
}

const LEAD_STATUS: Record<string, string> = {
  NEW: "New",
  CONTACTED: "Contacted",
  QUALIFIED: "Qualified",
  UNQUALIFIED: "Not a fit",
  BOOKED: "Booked",
  LOST: "Lost",
  CONVERTED: "Converted",
};
export const LEAD_STATUS_ORDER = ["NEW", "CONTACTED", "QUALIFIED", "BOOKED", "CONVERTED", "UNQUALIFIED", "LOST"];
export const leadStatusLabel = (s: string) => LEAD_STATUS[s] ?? titleCase(s.toLowerCase());

const SOURCE: Record<string, string> = { WEB: "Website", CHAT: "Website chat", PHONE: "Phone", REFERRAL: "Referral", IMPORT: "Import" };
export const sourceLabel = (s: string) => SOURCE[s] ?? titleCase(s.toLowerCase());

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

const AUTONOMY: Record<string, string> = {
  OBSERVE: "Watches only — takes no action",
  RECOMMEND: "Suggests — a person decides",
  EXECUTE_WITH_APPROVAL: "Acts only after you approve",
  EXECUTE_AUTONOMOUS: "Acts on its own within its permissions",
};
export const autonomyLabel = (t: string) => AUTONOMY[t] ?? titleCase(t.toLowerCase());

const AGENT_STATUS: Record<string, string> = { DRAFT: "Draft — not running", ACTIVE: "Active", PAUSED: "Paused", ARCHIVED: "Archived" };
export const agentStatusLabel = (s: string) => AGENT_STATUS[s] ?? titleCase(s.toLowerCase());
