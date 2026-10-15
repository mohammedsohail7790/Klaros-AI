/** Plain-language wording for the Medical Tourism pipeline, referral gate and task codes. The backend sends closed codes; this is the only place they become sentences. */
import type { PipelineStage } from "@/lib/api";

export const STAGE_LABEL: Record<PipelineStage, string> = {
  NEW_ENQUIRY: "New enquiry",
  CONSENT: "Consent",
  QUALIFICATION: "Qualification",
  PROVIDER_OPTIONS: "Provider options",
  PATIENT_APPROVAL: "Patient approval",
  REFERRED: "Referred",
  CLINIC_RESPONSE: "Clinic response",
  OUTCOME: "Outcome",
};

export const STAGE_ORDER: PipelineStage[] = ["NEW_ENQUIRY", "CONSENT", "QUALIFICATION", "PROVIDER_OPTIONS", "PATIENT_APPROVAL", "REFERRED", "CLINIC_RESPONSE", "OUTCOME"];

/** Why a referral cannot be sent right now (gate / send error codes). */
export const GATE_REASON: Record<string, string> = {
  no_share_consent: "The patient has not agreed to share their information with a clinic.",
  no_provider_approval: "The patient has not approved sharing with this clinic.",
  approval_withdrawn: "The patient withdrew their approval for this clinic.",
  approval_declined: "The patient declined to share with this clinic.",
  approval_not_informed: "The approval was not recorded as an informed decision.",
  no_personal_data_consent: "The patient no longer agrees to us keeping their details.",
  no_medical_consent: "The treatment cannot be shared: the patient has not agreed to us keeping it.",
  fields_not_permitted: "Some of the information is not on the patient's approved list.",
  provider_not_verified: "This clinic is not currently verified.",
  provider_inactive: "This clinic is not active.",
  provider_not_found: "This clinic could not be found.",
  referral_cancelled: "This referral was cancelled.",
  lead_erased: "This patient's details were erased.",
  lead_not_found: "This patient could not be found.",
  transport_failed: "The clinic could not be reached. Nothing was marked as sent; it is safe to try again.",
  transport_not_configured: "No clinic channel is set up yet. Referrals are recorded in the sandbox only.",
};

export const gateReason = (code: string | null | undefined): string => (code ? GATE_REASON[code] ?? "This referral cannot be sent right now." : "");

export const FIELD_LABEL: Record<string, string> = {
  name: "Name",
  phone: "Phone number",
  email: "Email",
  preferred_language: "Preferred language",
  preferred_contact_time: "Best time to contact",
  destination: "Destination",
  timeframe: "When they plan to travel",
  procedure: "Treatment (health-related)",
};

export const SHAREABLE_FIELDS = Object.keys(FIELD_LABEL);

export const TASK_KIND_LABEL: Record<string, string> = {
  escalation: "Needs a person",
  new_enquiry: "Review the new enquiry and match a clinic",
  provider_options: "Prepare provider options",
  halla_handoff: "Handed over by the AI receptionist",
  referral_followup: "Referral follow-up",
};

export const TASK_REASON_LABEL: Record<string, string> = {
  new_enquiry: "A new enquiry arrived through your website. Check it, then line up clinic options.",
  consent_withdrawn_after_send: "The patient withdrew consent after their details were sent: ask the clinic to remove them.",
  halla_escalated: "The AI receptionist asked for a person.",
  emergency: "Possible emergency raised on a call.",
  urgent: "The caller said it was urgent.",
  human_requested: "The caller asked to speak to a person.",
  complaint: "The caller raised a complaint.",
  clinical_question: "The caller asked a medical question the AI must not answer.",
  billing: "The caller asked about billing or payment.",
  other: "The AI receptionist asked for a person.",
};

export const OUTCOME_LABEL: Record<string, string> = {
  TREATMENT_COMPLETED: "Treatment completed",
  CONSULTATION_ATTENDED: "Consultation attended",
  NO_TREATMENT: "No treatment",
  PATIENT_DID_NOT_ATTEND: "Patient did not attend",
};

export const VERIFICATION_STATUSES = ["UNVERIFIED", "UNDER_REVIEW", "VERIFIED", "SUSPENDED", "EXPIRED"] as const;
export const VERIFICATION_LABEL: Record<string, string> = {
  UNVERIFIED: "Unverified",
  UNDER_REVIEW: "Under review",
  VERIFIED: "Verified",
  SUSPENDED: "Suspended",
  EXPIRED: "Expired",
};

/** "TR" becomes "Türkiye" (or the viewer's language); anything that is not a two-letter code is shown as given. */
export function countryName(code: string | null | undefined): string {
  if (!code) return "";
  if (!/^[A-Za-z]{2}$/.test(code)) return code;
  try {
    return new Intl.DisplayNames(undefined, { type: "region" }).of(code.toUpperCase()) ?? code;
  } catch {
    return code;
  }
}

/** Whether a clinic can receive referrals right now, and if not what to do about it. Mirrors the backend rule: active and verified. */
export function referableNote(status: string | undefined, verification: string | undefined): { ok: boolean; text: string } {
  if (status !== "ACTIVE") return { ok: false, text: "Inactive: cannot receive referrals." };
  switch (verification ?? "UNVERIFIED") {
    case "VERIFIED":
      return { ok: true, text: "Can receive referrals" };
    case "UNDER_REVIEW":
      return { ok: false, text: "Under review: finish checking it to refer patients." };
    case "SUSPENDED":
      return { ok: false, text: "Suspended: cannot receive referrals." };
    case "EXPIRED":
      return { ok: false, text: "Credentials expired: renew them to refer patients." };
    default:
      return { ok: false, text: "Not verified: check its credentials before referring patients." };
  }
}

/** The three things a patient can agree to, in the words an owner would use. The backend names are the closed consent scopes. */
export const CONSENT_SCOPES: { key: string; label: string; help: string }[] = [
  { key: "contact", label: "We may contact them", help: "By phone, email or message about their enquiry." },
  { key: "store_personal_data", label: "We may keep their details", help: "Name, phone, email and what they asked for." },
  { key: "store_medical_information", label: "We may keep their health information", help: "The treatment they are asking about." },
];

const SCOPE_WORDS: Record<string, string> = {
  contact: "contact them",
  store_personal_data: "keep their details",
  store_medical_information: "keep their health information",
};

/** "keep their details and keep their health information" for a list of missing consent scopes. */
export function missingConsentSentence(scopes: unknown): string {
  const list = Array.isArray(scopes) ? scopes.map((s) => SCOPE_WORDS[String(s)] ?? String(s)) : [];
  return list.length === 0 ? "record their consent" : list.join(" and ");
}

export const COMMISSION_STATUS_LABEL: Record<string, string> = { PENDING: "Potential", CONFIRMED: "Earned", INVOICED: "Invoiced", PAID: "Paid", DISPUTED: "Disputed", CANCELLED: "Cancelled" };

export const COMMISSION_STATUS_HELP: Record<string, string> = {
  PENDING: "Not earned yet. It becomes earned only when the clinic has reported the outcome your agreement names, with evidence.",
  CONFIRMED: "Earned. Send the clinic an invoice, then mark it invoiced.",
  INVOICED: "Invoice sent. Mark it paid when the money arrives.",
  PAID: "Payment received.",
  DISPUTED: "Under dispute. Return it to earned or invoiced once it is settled.",
  CANCELLED: "Will not be earned.",
};

/** Why a commission step was refused, in words. The backend sends a closed code. */
export const COMMISSION_REASON: Record<string, string> = {
  no_outcome_evidence: "The clinic has not reported the outcome your agreement names, with evidence, so this cannot be marked earned yet.",
  invalid_transition: "That step is not allowed from the commission's current status.",
  commission_exists: "This referral already has a commission.",
  currency_required: "Choose a currency.",
  commission_percentage_required: "Enter the commission percentage.",
  flat_amount_required: "Enter the fixed fee.",
  referral_cancelled: "This referral was cancelled.",
  unknown_outcome: "Choose the outcome your agreement names.",
};
