from enum import StrEnum


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    STAFF = "STAFF"
    TECHNICIAN = "TECHNICIAN"
    ACCOUNTANT = "ACCOUNTANT"
    READ_ONLY = "READ_ONLY"


class Permission(StrEnum):
    READ_CUSTOMERS = "READ_CUSTOMERS"
    CREATE_CUSTOMER = "CREATE_CUSTOMER"
    UPDATE_CUSTOMER = "UPDATE_CUSTOMER"
    READ_JOBS = "READ_JOBS"
    CREATE_JOB = "CREATE_JOB"
    SCHEDULE_JOB = "SCHEDULE_JOB"
    APPROVE_SPEND = "APPROVE_SPEND"
    CREATE_INVOICE = "CREATE_INVOICE"
    SEND_INVOICE = "SEND_INVOICE"
    # Phase 14: quotes/estimates. Reading a quote reuses VIEW_FINANCIALS
    # (same tier as reading an invoice) rather than a new READ_QUOTES.
    CREATE_QUOTE = "CREATE_QUOTE"
    SEND_QUOTE = "SEND_QUOTE"
    COLLECT_PAYMENT = "COLLECT_PAYMENT"
    VIEW_FINANCIALS = "VIEW_FINANCIALS"
    RUN_MARKETING = "RUN_MARKETING"
    SEND_CUSTOMER_MESSAGE = "SEND_CUSTOMER_MESSAGE"
    MANAGE_INTEGRATIONS = "MANAGE_INTEGRATIONS"
    MANAGE_USERS = "MANAGE_USERS"
    EXECUTE_AI_ACTION = "EXECUTE_AI_ACTION"
    DELETE_CUSTOMER = "DELETE_CUSTOMER"
    READ_LEADS = "READ_LEADS"
    CREATE_LEAD = "CREATE_LEAD"
    UPDATE_LEAD = "UPDATE_LEAD"
    QUALIFY_LEAD = "QUALIFY_LEAD"
    READ_APPOINTMENTS = "READ_APPOINTMENTS"
    CREATE_APPOINTMENT = "CREATE_APPOINTMENT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"
    UPDATE_JOB = "UPDATE_JOB"
    ASSIGN_JOB = "ASSIGN_JOB"
    DISPATCH_JOB = "DISPATCH_JOB"
    MANAGE_TASKS = "MANAGE_TASKS"
    MANAGE_MATERIALS = "MANAGE_MATERIALS"
    UPLOAD_JOB_ATTACHMENT = "UPLOAD_JOB_ATTACHMENT"
    MANAGE_QA = "MANAGE_QA"
    MANAGE_EXCEPTIONS = "MANAGE_EXCEPTIONS"
    APPROVE_SCOPE_CHANGE = "APPROVE_SCOPE_CHANGE"
    CLOSE_JOB = "CLOSE_JOB"
    DELETE_JOB = "DELETE_JOB"
    UPDATE_INVOICE = "UPDATE_INVOICE"
    APPROVE_INVOICE = "APPROVE_INVOICE"
    VOID_INVOICE = "VOID_INVOICE"
    RECORD_PAYMENT = "RECORD_PAYMENT"
    APPROVE_REFUND = "APPROVE_REFUND"
    APPROVE_CREDIT_NOTE = "APPROVE_CREDIT_NOTE"
    APPROVE_WRITEOFF = "APPROVE_WRITEOFF"
    MANAGE_JOB_COSTS = "MANAGE_JOB_COSTS"
    MANAGE_VENDORS = "MANAGE_VENDORS"
    MANAGE_COLLECTIONS = "MANAGE_COLLECTIONS"
    VIEW_CASH_FORECAST = "VIEW_CASH_FORECAST"
    READ_MARKETING = "READ_MARKETING"
    MANAGE_CAMPAIGNS = "MANAGE_CAMPAIGNS"
    MANAGE_MARKETING_CONTENT = "MANAGE_MARKETING_CONTENT"
    APPROVE_MARKETING_CONTENT = "APPROVE_MARKETING_CONTENT"
    MANAGE_OUTBOUND = "MANAGE_OUTBOUND"
    MANAGE_NURTURE = "MANAGE_NURTURE"
    MANAGE_REACTIVATION = "MANAGE_REACTIVATION"
    VIEW_MARKETING_ANALYTICS = "VIEW_MARKETING_ANALYTICS"
    READ_RETENTION = "READ_RETENTION"
    MANAGE_RETENTION = "MANAGE_RETENTION"
    MANAGE_RETENTION_CAMPAIGNS = "MANAGE_RETENTION_CAMPAIGNS"
    SEND_RETENTION_COMMUNICATION = "SEND_RETENTION_COMMUNICATION"
    MANAGE_REVIEWS = "MANAGE_REVIEWS"
    MANAGE_REFERRALS = "MANAGE_REFERRALS"
    APPROVE_REFERRAL_REWARD = "APPROVE_REFERRAL_REWARD"
    VIEW_CUSTOMER_HEALTH = "VIEW_CUSTOMER_HEALTH"
    VIEW_REFERRAL_ANALYTICS = "VIEW_REFERRAL_ANALYTICS"
    READ_EVENTS = "READ_EVENTS"
    MANAGE_EVENTS = "MANAGE_EVENTS"
    READ_MORNING_BRIEF = "READ_MORNING_BRIEF"
    GENERATE_MORNING_BRIEF = "GENERATE_MORNING_BRIEF"
    EXECUTE_RECOMMENDATION = "EXECUTE_RECOMMENDATION"
    READ_APPROVALS = "READ_APPROVALS"
    APPROVE_ACTIONS = "APPROVE_ACTIONS"
    REJECT_ACTIONS = "REJECT_ACTIONS"
    EXECUTE_APPROVED_ACTIONS = "EXECUTE_APPROVED_ACTIONS"
    READ_AI_ACTIVITY = "READ_AI_ACTIVITY"
    READ_AUTOMATION_POLICIES = "READ_AUTOMATION_POLICIES"
    MANAGE_AUTOMATION_POLICIES = "MANAGE_AUTOMATION_POLICIES"
    READ_KNOWLEDGE = "READ_KNOWLEDGE"
    MANAGE_KNOWLEDGE = "MANAGE_KNOWLEDGE"
    READ_NOTIFICATIONS = "READ_NOTIFICATIONS"
    MANAGE_NOTIFICATION_PREFERENCES = "MANAGE_NOTIFICATION_PREFERENCES"
    READ_VOICE_CALLS = "READ_VOICE_CALLS"
    MANAGE_VOICE_SETTINGS = "MANAGE_VOICE_SETTINGS"
    READ_AUTOMATIONS = "READ_AUTOMATIONS"
    MANAGE_AUTOMATIONS = "MANAGE_AUTOMATIONS"
    READ_MEMORY = "READ_MEMORY"
    MANAGE_MEMORY = "MANAGE_MEMORY"
    READ_COMPLIANCE = "READ_COMPLIANCE"
    MANAGE_COMPLIANCE = "MANAGE_COMPLIANCE"
    # Phase 1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.2): curation of the
    # tenant-independent IntegrationProviderCatalog reference table.
    # Deliberately kept separate from the existing, tenant-scoped
    # MANAGE_INTEGRATIONS (which gates a tenant's own connect/disconnect of
    # a specific IntegrationConnection) per
    # KLAROS_ARCHITECTURE_RECONCILIATION.md #3 — conflating the two would
    # either over-grant catalog-editing to every org admin or under-grant
    # integration-connecting to roles that need it.
    MANAGE_INTEGRATIONS_CATALOG = "MANAGE_INTEGRATIONS_CATALOG"
    # Phase 2 (KLAROS_BUSINESS_DISCOVERY_SPEC.md, KLAROS_BUSINESS_BLUEPRINT_SPEC.md,
    # KLAROS_ARCHITECTURE_RECONCILIATION.md #2): Business Discovery + Business
    # Blueprint permissions. `MANAGE_BLUEPRINT` is the exact permission name
    # the reconciliation mandates for gating blueprint section edits and
    # claim confirm/reject — granted to OWNER/ADMIN/MANAGER, matching the
    # existing MANAGE_MEMORY pattern (reconciliation's explicit guidance).
    # `READ_BLUEPRINT`/`READ_BUSINESS_DISCOVERY`/`MANAGE_BUSINESS_DISCOVERY`
    # are this implementation's own additions (not literally named in any
    # doc), added for the same read/manage split every other domain in this
    # file already uses (READ_MEMORY/MANAGE_MEMORY, READ_KNOWLEDGE/
    # MANAGE_KNOWLEDGE, ...).
    READ_BUSINESS_DISCOVERY = "READ_BUSINESS_DISCOVERY"
    MANAGE_BUSINESS_DISCOVERY = "MANAGE_BUSINESS_DISCOVERY"
    READ_BLUEPRINT = "READ_BLUEPRINT"
    MANAGE_BLUEPRINT = "MANAGE_BLUEPRINT"
    # Phase 3 (Recommendation Engine): distinct from the pre-existing
    # `Permission.EXECUTE_RECOMMENDATION` (app/models/morning_brief.py's
    # MorningBriefRecommendation — a different, already-shipped concept).
    # `READ_RECOMMENDATIONS`/`MANAGE_RECOMMENDATIONS` follow the same
    # read/manage split every other domain in this file uses
    # (READ_BLUEPRINT/MANAGE_BLUEPRINT, READ_MEMORY/MANAGE_MEMORY, ...).
    # `MANAGE_RECOMMENDATIONS` gates run-generation and accept/reject —
    # one canonical name, never a second competing
    # EDIT_RECOMMENDATIONS/ADMIN_RECOMMENDATIONS.
    READ_RECOMMENDATIONS = "READ_RECOMMENDATIONS"
    MANAGE_RECOMMENDATIONS = "MANAGE_RECOMMENDATIONS"
    # Phase 13 (Business Orchestration Foundation): the thin journey
    # coordinator sitting above Discovery/Blueprint/Recommendations. Its own
    # read/manage split, same convention as every other domain in this file
    # (READ_BLUEPRINT/MANAGE_BLUEPRINT, READ_RECOMMENDATIONS/
    # MANAGE_RECOMMENDATIONS, ...) — a new pair is justified here (rather
    # than reusing e.g. MANAGE_BUSINESS_DISCOVERY) because a journey action
    # can trigger Blueprint activation and Recommendation generation too, so
    # no single existing subsystem permission covers it; the journey layer
    # never uses this permission to bypass a subsystem's own permission
    # check (it still calls BusinessBlueprintService/RecommendationService
    # directly, which enforce tenant scoping themselves).
    READ_BUSINESS_JOURNEY = "READ_BUSINESS_JOURNEY"
    MANAGE_BUSINESS_JOURNEY = "MANAGE_BUSINESS_JOURNEY"
    # Phase 4 (Agent Runtime, KLAROS_FINAL_API_ARCHITECTURE.md's
    # `/api/v1/agents` table): `MANAGE_AGENTS` gates create/edit/publish/
    # pause/archive lifecycle actions and tool-permission grants;
    # `EXECUTE_AGENT` gates POST /agents/{id}/execute specifically — kept
    # separate from MANAGE_AGENTS because a role that can trigger a run
    # (e.g. STAFF) should not necessarily be able to redefine what the
    # agent is allowed to do. Deliberately distinct from the pre-existing
    # `Permission.EXECUTE_RECOMMENDATION` (app/models/morning_brief.py's
    # MorningBriefRecommendation — a different, already-shipped concept
    # this phase must never touch or be confused with, per this phase's
    # own instructions). `READ_AGENTS`/`READ_AGENT_EXECUTIONS` follow the
    # same read/manage split every other domain in this file uses.
    READ_AGENTS = "READ_AGENTS"
    MANAGE_AGENTS = "MANAGE_AGENTS"
    EXECUTE_AGENT = "EXECUTE_AGENT"
    READ_AGENT_EXECUTIONS = "READ_AGENT_EXECUTIONS"
    # Phase 9 (MCP server exposure): a single permission gates BOTH halves
    # of the admin-facing MCP surface — deciding which already-governed
    # tools are allowlisted for external MCP exposure, and issuing/revoking
    # the scoped credentials external MCP clients authenticate with. Both
    # are the same class of action (granting a non-human caller reach into
    # governed tools) as the existing MANAGE_AGENTS/MANAGE_INTEGRATIONS_
    # CATALOG permissions, so this follows the same "one canonical
    # permission, no read/manage split" shape MANAGE_INTEGRATIONS_CATALOG
    # already uses — there is no meaningful "read-only" version of "can see
    # which tools are MCP-exposed" that isn't itself security-sensitive
    # (it reveals the tenant's external attack surface), so it is not
    # split into READ_/MANAGE_ the way most other domains in this file are.
    # Deliberately NOT granted to MANAGER/STAFF/ACCOUNTANT/TECHNICIAN/
    # READ_ONLY below — exposing tools to an external, potentially
    # adversarial client is an OWNER/ADMIN-tier decision only.
    MANAGE_MCP_SERVER = "MANAGE_MCP_SERVER"
    # Phase 10 (Medical Tourism vertical extension): gates the new
    # `/api/v1/medical-tourism/*` domain (providers, credentials,
    # procedures, offerings, patient leads, consultations, referral
    # commissions) and its ToolRegistry tools. Follows the same
    # read/manage split every other domain in this file uses
    # (READ_BLUEPRINT/MANAGE_BLUEPRINT, READ_RECOMMENDATIONS/
    # MANAGE_RECOMMENDATIONS, ...) — READ_MEDICAL_TOURISM for list/get,
    # MANAGE_MEDICAL_TOURISM for create/update of providers, credentials,
    # procedures, and offerings. Deliberately vertical-domain-scoped, not
    # a generic "MANAGE_VERTICAL_DATA" — mirrors how MANAGE_RETENTION is
    # its own domain's permission, not a generic CRM one.
    READ_MEDICAL_TOURISM = "READ_MEDICAL_TOURISM"
    MANAGE_MEDICAL_TOURISM = "MANAGE_MEDICAL_TOURISM"
    # Phase 11 (Website Builder, PHASE_11_WEBSITE_BUILDER_DESIGN.md §10 RBAC):
    # three permissions, not the usual two-way read/manage split — mirrors
    # how MANAGE_AGENTS/EXECUTE_AGENT are kept separate (Phase 4's own
    # reasoning): a role that can edit a draft website
    # (`MANAGE_WEBSITE`: create/update pages/sections/theme/generate) should
    # not automatically be able to make it live (`PUBLISH_WEBSITE`) — the
    # same "editing != releasing" split this codebase already applies to
    # MANAGE_MARKETING_CONTENT vs APPROVE_MARKETING_CONTENT and
    # UPDATE_INVOICE vs APPROVE_INVOICE. `READ_WEBSITE` gates list/get/
    # preview.
    READ_WEBSITE = "READ_WEBSITE"
    MANAGE_WEBSITE = "MANAGE_WEBSITE"
    PUBLISH_WEBSITE = "PUBLISH_WEBSITE"


# Permission matrix: role -> allowed permissions.
# OWNER/ADMIN get everything; DELETE_CUSTOMER is OWNER-only (section 4/10: destructive ops are
# BLOCKED or OWNER ONLY).
_ALL_PERMISSIONS = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.OWNER: _ALL_PERMISSIONS,
    Role.ADMIN: _ALL_PERMISSIONS - {Permission.DELETE_CUSTOMER},
    Role.MANAGER: {
        Permission.READ_CUSTOMERS,
        Permission.CREATE_CUSTOMER,
        Permission.UPDATE_CUSTOMER,
        Permission.READ_JOBS,
        Permission.CREATE_JOB,
        Permission.SCHEDULE_JOB,
        Permission.CREATE_INVOICE,
        Permission.SEND_INVOICE,
        Permission.CREATE_QUOTE,
        Permission.SEND_QUOTE,
        Permission.VIEW_FINANCIALS,
        Permission.RUN_MARKETING,
        Permission.SEND_CUSTOMER_MESSAGE,
        Permission.EXECUTE_AI_ACTION,
        Permission.READ_LEADS,
        Permission.CREATE_LEAD,
        Permission.UPDATE_LEAD,
        Permission.QUALIFY_LEAD,
        Permission.READ_APPOINTMENTS,
        Permission.CREATE_APPOINTMENT,
        Permission.CANCEL_APPOINTMENT,
        Permission.UPDATE_JOB,
        Permission.ASSIGN_JOB,
        Permission.DISPATCH_JOB,
        Permission.MANAGE_TASKS,
        Permission.MANAGE_MATERIALS,
        Permission.UPLOAD_JOB_ATTACHMENT,
        Permission.MANAGE_QA,
        Permission.MANAGE_EXCEPTIONS,
        Permission.APPROVE_SCOPE_CHANGE,
        Permission.CLOSE_JOB,
        Permission.UPDATE_INVOICE,
        Permission.APPROVE_INVOICE,
        Permission.VOID_INVOICE,
        Permission.RECORD_PAYMENT,
        Permission.APPROVE_REFUND,
        Permission.APPROVE_CREDIT_NOTE,
        Permission.APPROVE_WRITEOFF,
        Permission.MANAGE_JOB_COSTS,
        Permission.MANAGE_VENDORS,
        Permission.MANAGE_COLLECTIONS,
        Permission.VIEW_CASH_FORECAST,
        Permission.READ_MARKETING,
        Permission.MANAGE_CAMPAIGNS,
        Permission.MANAGE_MARKETING_CONTENT,
        Permission.APPROVE_MARKETING_CONTENT,
        Permission.MANAGE_OUTBOUND,
        Permission.MANAGE_NURTURE,
        Permission.MANAGE_REACTIVATION,
        Permission.VIEW_MARKETING_ANALYTICS,
        Permission.READ_RETENTION,
        Permission.MANAGE_RETENTION,
        Permission.MANAGE_RETENTION_CAMPAIGNS,
        Permission.SEND_RETENTION_COMMUNICATION,
        Permission.MANAGE_REVIEWS,
        Permission.MANAGE_REFERRALS,
        Permission.APPROVE_REFERRAL_REWARD,
        Permission.VIEW_CUSTOMER_HEALTH,
        Permission.VIEW_REFERRAL_ANALYTICS,
        Permission.READ_EVENTS,
        Permission.MANAGE_EVENTS,
        Permission.READ_MORNING_BRIEF,
        Permission.GENERATE_MORNING_BRIEF,
        Permission.EXECUTE_RECOMMENDATION,
        Permission.READ_APPROVALS,
        Permission.APPROVE_ACTIONS,
        Permission.REJECT_ACTIONS,
        Permission.EXECUTE_APPROVED_ACTIONS,
        Permission.READ_AI_ACTIVITY,
        Permission.READ_AUTOMATION_POLICIES,
        Permission.MANAGE_AUTOMATION_POLICIES,
        Permission.READ_NOTIFICATIONS,
        Permission.MANAGE_NOTIFICATION_PREFERENCES,
        Permission.READ_KNOWLEDGE,
        Permission.MANAGE_KNOWLEDGE,
        Permission.READ_VOICE_CALLS,
        Permission.MANAGE_VOICE_SETTINGS,
        Permission.READ_AUTOMATIONS,
        Permission.MANAGE_AUTOMATIONS,
        Permission.READ_MEMORY,
        Permission.MANAGE_MEMORY,
        Permission.READ_COMPLIANCE,
        Permission.MANAGE_COMPLIANCE,
        Permission.READ_BUSINESS_DISCOVERY,
        Permission.MANAGE_BUSINESS_DISCOVERY,
        Permission.READ_BLUEPRINT,
        Permission.MANAGE_BLUEPRINT,
        Permission.READ_RECOMMENDATIONS,
        Permission.MANAGE_RECOMMENDATIONS,
        Permission.READ_BUSINESS_JOURNEY,
        Permission.MANAGE_BUSINESS_JOURNEY,
        Permission.READ_AGENTS,
        Permission.MANAGE_AGENTS,
        Permission.EXECUTE_AGENT,
        Permission.READ_AGENT_EXECUTIONS,
        Permission.READ_MEDICAL_TOURISM,
        Permission.MANAGE_MEDICAL_TOURISM,
        Permission.READ_WEBSITE,
        Permission.MANAGE_WEBSITE,
        Permission.PUBLISH_WEBSITE,
    },
    Role.STAFF: {
        Permission.READ_CUSTOMERS,
        Permission.CREATE_CUSTOMER,
        Permission.UPDATE_CUSTOMER,
        Permission.READ_JOBS,
        Permission.CREATE_JOB,
        Permission.SCHEDULE_JOB,
        Permission.SEND_CUSTOMER_MESSAGE,
        Permission.READ_LEADS,
        Permission.READ_BUSINESS_DISCOVERY,
        Permission.MANAGE_BUSINESS_DISCOVERY,
        Permission.READ_BLUEPRINT,
        Permission.READ_RECOMMENDATIONS,
        Permission.READ_BUSINESS_JOURNEY,
        Permission.READ_AGENTS,
        Permission.EXECUTE_AGENT,
        Permission.READ_AGENT_EXECUTIONS,
        Permission.CREATE_LEAD,
        Permission.UPDATE_LEAD,
        Permission.READ_APPOINTMENTS,
        Permission.CREATE_APPOINTMENT,
        Permission.CANCEL_APPOINTMENT,
        Permission.UPDATE_JOB,
        Permission.MANAGE_TASKS,
        Permission.MANAGE_MATERIALS,
        Permission.UPLOAD_JOB_ATTACHMENT,
        # STAFF can view the vertical directory (e.g. to reference a
        # provider while working a patient lead) but must not mutate
        # vertical configuration merely by having generic platform access.
        Permission.READ_MEDICAL_TOURISM,
        # STAFF can view and edit a draft website, but publishing (making it
        # the tenant's live, customer-facing site) is a MANAGER-tier
        # decision, not granted here — the same "can edit, cannot release"
        # split UPDATE_INVOICE (STAFF has no invoice permissions at all,
        # but the same pattern) vs. APPROVE_INVOICE (MANAGER-only) applies
        # elsewhere in this matrix.
        Permission.READ_WEBSITE,
        Permission.MANAGE_WEBSITE,
    },
    Role.TECHNICIAN: {
        Permission.READ_JOBS,
        Permission.SCHEDULE_JOB,
        Permission.READ_CUSTOMERS,
        Permission.READ_LEADS,
        Permission.READ_APPOINTMENTS,
        Permission.UPDATE_JOB,
        Permission.DISPATCH_JOB,
        Permission.MANAGE_TASKS,
        Permission.UPLOAD_JOB_ATTACHMENT,
        Permission.MANAGE_QA,
    },
    Role.ACCOUNTANT: {
        Permission.VIEW_FINANCIALS,
        Permission.CREATE_INVOICE,
        Permission.UPDATE_INVOICE,
        Permission.SEND_INVOICE,
        Permission.CREATE_QUOTE,
        Permission.SEND_QUOTE,
        Permission.COLLECT_PAYMENT,
        Permission.RECORD_PAYMENT,
        Permission.MANAGE_JOB_COSTS,
        Permission.MANAGE_VENDORS,
        Permission.MANAGE_COLLECTIONS,
        Permission.VIEW_CASH_FORECAST,
        Permission.READ_CUSTOMERS,
        Permission.READ_LEADS,
        Permission.READ_JOBS,
    },
    Role.READ_ONLY: {
        Permission.READ_CUSTOMERS,
        Permission.READ_JOBS,
        Permission.VIEW_FINANCIALS,
        Permission.READ_LEADS,
        Permission.READ_APPOINTMENTS,
        Permission.VIEW_CASH_FORECAST,
        Permission.READ_MARKETING,
        Permission.VIEW_MARKETING_ANALYTICS,
        Permission.READ_RETENTION,
        Permission.VIEW_CUSTOMER_HEALTH,
        Permission.VIEW_REFERRAL_ANALYTICS,
        Permission.READ_EVENTS,
        Permission.READ_MORNING_BRIEF,
        Permission.READ_APPROVALS,
        Permission.READ_AI_ACTIVITY,
        Permission.READ_AUTOMATION_POLICIES,
        Permission.READ_NOTIFICATIONS,
        Permission.READ_KNOWLEDGE,
        Permission.READ_VOICE_CALLS,
        Permission.READ_AUTOMATIONS,
        Permission.READ_MEMORY,
        Permission.READ_COMPLIANCE,
        Permission.READ_BUSINESS_DISCOVERY,
        Permission.READ_BLUEPRINT,
        Permission.READ_RECOMMENDATIONS,
        Permission.READ_BUSINESS_JOURNEY,
        Permission.READ_AGENTS,
        Permission.READ_AGENT_EXECUTIONS,
        Permission.READ_MEDICAL_TOURISM,
        Permission.READ_WEBSITE,
    },
}


def role_has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())
