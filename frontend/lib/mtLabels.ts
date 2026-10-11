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
  provider_options: "Prepare provider options",
  halla_handoff: "Handed over by the AI receptionist",
  referral_followup: "Referral follow-up",
};

export const TASK_REASON_LABEL: Record<string, string> = {
  consent_withdrawn_after_send: "The patient withdrew consent after their details were sent: ask the clinic to remove them.",
  halla_escalated: "The AI receptionist asked for a person.",
  emergency: "Possible emergency raised on a call.",
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
