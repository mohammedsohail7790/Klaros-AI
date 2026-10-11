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
    integration_groups: ["CRM & operations", "Communication", "Calendar", "Payments", "Accounting", "Marketing", "Ecommerce", "Analytics", "AI Workforce"],
    attention: [],
    module: {
      metrics: [{ key: "patient_leads", label: "Patient enquiries", value: 2, route: "/medical-tourism/leads" }],
      data: [{ key: "providers", label: "Providers", count: 2, route: "/medical-tourism/providers" }, { key: "procedures", label: "Procedures", count: 0, route: "/medical-tourism/procedures" }],
      breakdowns: [{ key: "providers_by_country", label: "Providers by country", items: [{ label: "IN", value: 2 }] }],
    },
    starter_workflow_exists: true,
    ai: { events: {}, interactions: 0, qualified: 0, escalated: 0, needs_person: 0 },
    ...over,
  };
}

export const emptyLeads = { total: 0, new_7d: 0, by_status: {}, by_source: {}, qualified: 0, website_enquiries: 0, waiting: [], recent: [] };

/** A complete, realistic BuilderOverview payload. */
export function overviewFixture(over: Record<string, unknown> = {}) {
  return {
    journey: { id: "j1", status: "COMPLETED" },
    blueprint: { id: "b1", version: 1, status: "ACTIVE" },
    business: { name: "Aurora Medical Travel", summary: "Medical travel", industry: "Medical tourism", business_model: "Commission", customers: "Patients from the Gulf" },
    requirements: [
      { key: "provider_directory", label: "Provider directory", group: "operations", required: true, workforce_addressable: false, providers: [] },
      { key: "appointment_scheduling", label: "Scheduling", group: "operations", required: true, workforce_addressable: true,
        providers: [{ provider_key: "google_calendar", display_name: "Google Calendar", implementation_status: "REAL", state: "NOT_CONNECTED", adapter_required: false }] },
      { key: "payment_processing", label: "Payments", group: "money", required: true, workforce_addressable: false,
        providers: [{ provider_key: "stripe", display_name: "Stripe", implementation_status: "REAL", state: "CONNECTED", adapter_required: false }, { provider_key: "paypal", display_name: "PayPal", implementation_status: "STUB", state: "PLANNED", adapter_required: true }] },
    ],
    business_map: {
      lanes: ["Customers", "Front door", "Klaros", "Operations", "Money & growth", "Systems & partners"],
      nodes: [
        { id: "actor:customers", kind: "actor", label: "Your customers", sublabel: null, lane: 0, state: "READY", why: null, route: null, planned: false },
        { id: "cap:website", kind: "capability", label: "Website", sublabel: "Required", lane: 1, state: "READY", why: null, route: "/website", planned: false },
        { id: "workforce:ai", kind: "workforce", label: "AI workforce", sublabel: "Halla", lane: 1, state: "INTEGRATION_REQUIRED", why: null, route: "/workforce", planned: true },
        { id: "core:klaros", kind: "core", label: "Klaros", sublabel: null, lane: 2, state: "READY", why: null, route: null, planned: false },
        { id: "cap:lead_capture", kind: "capability", label: "Lead capture", sublabel: "Required", lane: 3, state: "READY", why: null, route: "/leads", planned: false },
        { id: "cap:provider_directory", kind: "capability", label: "Provider directory", sublabel: "Required", lane: 3, state: "READY", why: null, route: "/medical-tourism/providers", planned: false },
        { id: "cap:analytics", kind: "capability", label: "Analytics", sublabel: "Required", lane: 4, state: "READY", why: null, route: "/dashboard", planned: false },
        { id: "outcome:revenue", kind: "outcome", label: "Revenue", sublabel: null, lane: 4, state: "READY", why: null, route: null, planned: false },
      ],
      edges: [],
    },
    next_actions: [],
    launch: {
      items: [
        { key: "blueprint", label: "Business Blueprint", status: "READY", detail: "Confirmed.", required: true, route: "/business/blueprint", done: true },
        { key: "requirements", label: "Requirements", status: "READY", detail: "3 requirements.", required: true, route: "/business/requirements", done: true },
        { key: "website", label: "Website", status: "READY", detail: "Your website is published.", required: true, route: "/website", done: true },
        { key: "data:providers", label: "Providers", status: "NOT_READY", detail: "Nothing has been added yet.", required: true, route: "/medical-tourism/providers", done: false },
      ],
      ready: false, verdict: "NOT_READY", blocking: [], launched: true,
    },
    progress: [],
    stages: [],
    website: { exists: true, published: true, has_draft: false, pages: ["home"] },
    workforce: { provider: "halla", status: "NOT_CONNECTED", adapter_implemented: false, message: "No AI workforce is connected.", agent_id: null, mode: "none", capabilities: [] },
    operations: { lead_count: 4, modules: [] },
    modules: [],
    ...over,
  };
}
