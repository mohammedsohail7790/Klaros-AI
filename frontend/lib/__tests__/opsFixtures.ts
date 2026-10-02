/** Shared fixture for operating-layer tests: a complete, realistic BusinessOperations payload. */
export function opsFixture(over: Record<string, unknown> = {}) {
  return {
    leads: {
      total: 4, new_7d: 3, by_status: { NEW: 2, QUALIFIED: 1, CONVERTED: 1 }, by_source: { WEB: 3, CHAT: 1 }, qualified: 2, website_enquiries: 3,
      waiting: [{ id: "l1", name: "Layla Patient", source: "WEB", created_at: new Date().toISOString() }],
      recent: [{ id: "l1", name: "Layla Patient", status: "NEW", source: "WEB", created_at: new Date().toISOString() }],
    },
    customers: 1,
    workflows: [
      { id: "w1", name: "New lead alert", description: "Alert the team whenever a new lead arrives.", status: "ENABLED", trigger: "EVENT", trigger_event: "lead.created",
        actions: ["notifications.create_notification"], published: true, runs: 0, failed_runs: 0, last_run: null },
    ],
    lead_pipeline: [
      { key: "form", label: "Website form submitted", kind: "SYSTEM", state: "READY", detail: "Your published website accepts enquiries.", route: "/website" },
      { key: "notify", label: "Team notified", kind: "AUTOMATED", state: "READY", detail: "A workflow alerts your team about each new lead.", route: "/business/workflows" },
    ],
    agents: [],
    activity: [{ at: new Date().toISOString(), kind: "lead", text: "Lead received: Layla Patient", route: "/leads/l1" }],
    integrations: [
      { provider_key: "stripe", name: "Stripe", category: "Payments", purpose: "Payments.", capabilities: ["payment processing"], state: "AVAILABLE", implementation: "REAL", last_error: null },
      { provider_key: "google_calendar", name: "Google Calendar", category: "Calendar", purpose: null, capabilities: [], state: "CONNECTED", implementation: "REAL", last_error: null },
      { provider_key: "google_ads", name: "Google Ads", category: "Marketing", purpose: null, capabilities: [], state: "PLANNED", implementation: "STUB", last_error: null },
      { provider_key: "halla", name: "Halla AI", category: "AI Workforce", purpose: "Voice and calls.", capabilities: ["voice"], state: "INTEGRATION_REQUIRED", implementation: "EXTERNAL", last_error: null },
    ],
    integration_groups: ["CRM & operations", "Calendar", "Payments", "Marketing", "AI Workforce"],
    attention: [],
    module: {
      metrics: [{ key: "patient_leads", label: "Patient enquiries", value: 2, route: "/medical-tourism/leads" }],
      data: [{ key: "providers", label: "Providers", count: 2, route: "/medical-tourism/providers" }, { key: "procedures", label: "Procedures", count: 0, route: "/medical-tourism/procedures" }],
      breakdowns: [{ key: "providers_by_country", label: "Providers by country", items: [{ label: "IN", value: 2 }] }],
    },
    starter_workflow_exists: true,
    ...over,
  };
}

export const emptyLeads = { total: 0, new_7d: 0, by_status: {}, by_source: {}, qualified: 0, website_enquiries: 0, waiting: [], recent: [] };
