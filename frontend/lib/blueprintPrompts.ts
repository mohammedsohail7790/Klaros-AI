/**
 * Owner-facing wording for the Business Blueprint editor. A business owner should
 * answer a plain question in their own words, not type a "detail name" and "value"
 * or a raw capability key. This is presentation only: each answer is saved under
 * the same section/key the backend already stores.
 */

export interface SectionPrompt {
  /** The key the answer is stored under inside the section's data. */
  key: string;
  question: string;
  placeholder: string;
}

export const SECTION_PROMPTS: Record<string, SectionPrompt> = {
  REVENUE: { key: "revenue", question: "How does money come in?", placeholder: "For example: a referral fee from the clinic for each completed treatment, plus a fee for travel and visa coordination" },
  CHANNELS: { key: "channels", question: "How do customers find you?", placeholder: "For example: website enquiries, WhatsApp, Instagram, partner agents" },
  MARKETING: { key: "plan", question: "How do you promote the business?", placeholder: "For example: treatment guides for patients in the UAE and UK, paid ads on Meta and Google" },
  FINANCE: { key: "currencies", question: "Which currencies do you quote and get paid in?", placeholder: "For example: quotes in AED, GBP and USD; clinics pay commission in USD" },
  OPERATIONS: { key: "coordination", question: "Who does what, day to day?", placeholder: "For example: coordinators answer every enquiry within 24 hours" },
  COMMUNICATIONS: { key: "channels", question: "How do you talk to customers?", placeholder: "For example: phone, email and WhatsApp" },
  SUPPLIERS_PROVIDERS: { key: "providers", question: "Who do you work with to deliver the service?", placeholder: "For example: verified hospitals and clinics in India and Turkey" },
  COMPLIANCE: { key: "rules", question: "Which rules or licences must the business follow?", placeholder: "For example: patient consent before any data is shared with a clinic; no clinical advice" },
  GOALS: { key: "goals", question: "What are you aiming for?", placeholder: "For example: 50 referred patients in the first six months" },
  CONSTRAINTS: { key: "constraints", question: "What limits or must-nots apply?", placeholder: "For example: only verified clinics receive referrals; no prices unless a clinic has approved them" },
  ASSUMPTIONS: { key: "assumptions", question: "What are you assuming is true?", placeholder: "For example: patients will mostly arrive through partner agents" },
  DECISIONS: { key: "decisions", question: "What have you already decided?", placeholder: "For example: we start with dental and hair transplant only" },
};

/** What a business can need, in everyday words. Each phrase is recognised by Klaros and becomes a requirement. */
export const CAPABILITY_OPTIONS: string[] = [
  "Website",
  "Lead capture",
  "Lead qualification",
  "Customer records (CRM)",
  "Customer communication",
  "Appointment scheduling",
  "Provider directory",
  "Treatment catalog",
  "Referral workflow",
  "Commission tracking",
  "Multi-currency",
  "Payments",
  "Accounting",
  "Marketing",
  "Compliance",
  "Analytics",
];

/** A stored capability as a person reads it: "medical_tourism.provider_directory" becomes "Provider directory". */
export function friendlyCapability(raw: string): string {
  const last = raw.includes(".") ? raw.split(".").pop() ?? raw : raw;
  const text = last.replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : raw;
}

export function hasLine(text: string, line: string): boolean {
  return text.split(/\n|,/).some((l) => l.trim().toLowerCase() === line.toLowerCase());
}

export function toggleLine(text: string, line: string, on: boolean): string {
  const lines = text.split(/\n|,/).map((l) => l.trim()).filter(Boolean);
  if (on && lines.some((l) => l.toLowerCase() === line.toLowerCase())) return lines.join("\n");
  const without = lines.filter((l) => l.toLowerCase() !== line.toLowerCase());
  return (on ? [...without, line] : without).join("\n");
}
