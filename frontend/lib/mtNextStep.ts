/**
 * What a referral business should see at the top of a page that would otherwise be empty or generic: why the page is quiet, and the one next step.
 * Pure: it only turns counts the backend already derived into plain sentences.
 */
export type ClinicCounts = { total: number; withContact: number; verified: number };
export type NextStep = { title: string; body: string; action: { label: string; href: string } };

/** The step that unblocks the whole business: nothing can be referred until a clinic is verified. Empty counts are treated as "not known yet". */
function clinicStep(c: ClinicCounts | null): NextStep["action"] & { text: string } {
  if (c && c.total === 0) return { label: "Add a clinic", href: "/medical-tourism/providers", text: "No clinics are listed yet, so no patient can be matched." };
  if (c && c.verified === 0) {
    return { label: "Open clinics", href: "/medical-tourism/providers", text: `None of your ${c.total} clinics is verified yet (${c.withContact} have an email on file). A patient can only be referred to a verified clinic, so verifying one is the first step.` };
  }
  return { label: "Open patients", href: "/medical-tourism/pipeline", text: "Next: match a patient who has said yes to a verified clinic." };
}

export function pageNextStep(path: string, clinics: ClinicCounts | null): NextStep | null {
  const step = clinicStep(clinics);
  switch (path) {
    case "/finance/invoices":
      return { title: "Clinic invoices are created for you", body: `When a clinic confirms a treatment outcome with evidence, Klaros confirms the commission and drafts the invoice to that clinic. There is nothing to create by hand. ${step.text}`, action: { label: step.label, href: step.href } };
    case "/medical-tourism/referrals":
    case "/medical-tourism/revenue":
      return { title: "Money arrives after a clinic reports an outcome", body: `A commission appears only after a clinic reports an outcome for a referral you sent. ${step.text}`, action: { label: step.label, href: step.href } };
    case "/medical-tourism/provider-referrals":
      return { title: "A referral goes out only after the patient approves", body: `Klaros sends a referral once the patient's approval for that clinic is on record. ${step.text}`, action: { label: step.label, href: step.href } };
    case "/medical-tourism/procedures":
      return { title: "Procedures are how a patient is matched to a clinic", body: "A patient asks for a treatment such as dental work, a hair transplant or aesthetic surgery. Klaros finds verified clinics that offer that procedure, so list each treatment you arrange here, then say which clinic offers it.", action: { label: "Open clinics", href: "/medical-tourism/providers" } };
    case "/marketing":
      return {
        title: "Growth for a referral business has two sides",
        body: `Patients arrive from your website, and hospitals join as partners. The ad numbers below stay at zero until an ad account is connected. To bring hospitals in, run the clinic outreach campaign${clinics ? ` (${clinics.withContact} of ${clinics.total} clinics have an email on file)` : ""}.`,
        action: { label: "Open campaigns", href: "/marketing/campaigns" },
      };
    case "/marketing/campaigns":
      return { title: "Start with the clinic outreach campaign", body: "It writes to hospitals' published international-patient addresses from wording you approve. Nothing is sent until you press Go live.", action: { label: "Outreach wording", href: "/marketing/outreach-wording" } };
    default:
      return null;
  }
}
