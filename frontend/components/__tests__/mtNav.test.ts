import { describe, expect, it } from "vitest";
import { locate, visibleAreas } from "@/components/navAreas";

const labels = (modules: string[]) => Object.fromEntries(visibleAreas(modules).map((a) => [a.key, a.tabs.map((t) => t.label)]));

describe("the referral business's navigation", () => {
  it("keeps only the pages a commission-only referral business uses", () => {
    expect(labels(["medical_tourism"])).toEqual({
      home: ["Home", "Today"],
      crm: ["Leads", "Patients", "Consultations"],
      sales: ["Clinic agreements"],
      marketing: ["Overview", "Campaigns", "Content", "Outreach wording"],
      finance: ["Invoices", "Commissions", "Revenue"],
      operations: ["Replies", "Tasks", "Referrals"],
      clinics: ["Providers", "Procedures & offers"],
      ai: ["Receptionist", "Approvals", "Agents", "Knowledge", "Rules", "Activity"],
      website: ["Website"],
      business: ["Setup"],
      settings: ["Team", "Billing", "Integrations", "Compliance", "Account"],
    });
  });

  it("changes nothing for a business without the module", () => {
    const l = labels([]);
    expect(l.finance).toContain("Cash");
    expect(l.operations).toContain("Jobs");
    expect(l.marketing).not.toContain("Outreach wording");
    expect(l.crm).toContain("Customers");
  });

  it("still places a hidden page that is opened by its address", () => {
    const areas = visibleAreas(["medical_tourism"]);
    expect(locate("/marketing/outreach-wording", areas)?.area.key).toBe("marketing");
    expect(locate("/medical-tourism/patients/abc", areas)?.tab.label).toBe("Patients");
  });
});
