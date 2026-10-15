import {
  Home, Users, Wrench, DollarSign, Megaphone, Bot, Globe, Hammer, Building2, Settings2, FileText,
} from "lucide-react";

/** `module` names a business module (vertical extension) that must be enabled for the tenant to see this tab; `hideWithModule` hides a generic tab a module replaces (a commission-only business has no patient quotes). No `module` means every tenant sees it. */
export type AreaTab = { href: string; label: string; module?: string; hideWithModule?: string };
export type NavArea = { key: string; label: string; icon: any; tabs: AreaTab[] };

const MT = "medical_tourism";

/**
 * The product's navigation, the same for every business: a handful of areas, each with tabs. Every page keeps its own URL; an area only groups them.
 * What differs per tenant is its configuration: a module adds its own tabs to the areas where they belong (Medical Tourism adds Patients to CRM,
 * Tasks to Operations, Commissions to Finance, and the Clinics area), and an area with no visible tab is not shown.
 * A page belongs to the area of its most specific tab href (so /retention/reviews is under "Customer care" in Growth).
 * Developer-only pages such as /events are deliberately not listed: they stay reachable by URL.
 */
export const AREAS: NavArea[] = [
  { key: "home", label: "Home", icon: Home, tabs: [{ href: "/business/home", label: "Home" }, { href: "/medical-tourism/overview", label: "Today", module: MT }] },
  {
    key: "crm", label: "CRM", icon: Users,
    tabs: [
      { href: "/leads", label: "Leads" }, { href: "/customers", label: "Customers" }, { href: "/calendar", label: "Calendar" },
      { href: "/medical-tourism/pipeline", label: "Patients", module: MT }, { href: "/medical-tourism/consultations", label: "Consultations", module: MT },
    ],
  },
  {
    key: "sales", label: "Sales", icon: FileText,
    tabs: [
      { href: "/medical-tourism/agreements", label: "Clinic agreements", module: MT },
      { href: "/quotes", label: "Quotes", hideWithModule: MT }, { href: "/contracts", label: "Contracts", hideWithModule: MT },
    ],
  },
  {
    key: "marketing", label: "Marketing", icon: Megaphone,
    tabs: [
      { href: "/marketing", label: "Overview" }, { href: "/marketing/campaigns", label: "Campaigns" }, { href: "/marketing/content", label: "Content" },
      { href: "/marketing/seo", label: "SEO" }, { href: "/marketing/outbound", label: "Outbound" }, { href: "/marketing/nurture", label: "Nurture" },
      { href: "/marketing/reactivation", label: "Reactivation" }, { href: "/retention", label: "Customer care" },
    ],
  },
  {
    key: "finance", label: "Finance", icon: DollarSign,
    tabs: [
      { href: "/finance/invoices", label: "Invoices" }, { href: "/finance/ar", label: "Receivables" }, { href: "/finance/cash", label: "Cash" },
      { href: "/finance/profitability", label: "Profitability" }, { href: "/vendors", label: "Vendors" },
      { href: "/medical-tourism/referrals", label: "Commissions", module: MT }, { href: "/medical-tourism/revenue", label: "Revenue", module: MT },
    ],
  },
  {
    key: "operations", label: "Operations", icon: Wrench,
    tabs: [
      { href: "/operations", label: "Overview" }, { href: "/jobs", label: "Jobs" }, { href: "/operations/workers", label: "Workers" }, { href: "/exceptions", label: "Exceptions" }, { href: "/replies", label: "Replies" },
      { href: "/medical-tourism/tasks", label: "Tasks", module: MT }, { href: "/medical-tourism/provider-referrals", label: "Referrals", module: MT },
    ],
  },
  {
    key: "clinics", label: "Clinics", icon: Building2,
    tabs: [{ href: "/medical-tourism/providers", label: "Providers", module: MT }, { href: "/medical-tourism/procedures", label: "Procedures & offers", module: MT }],
  },
  {
    key: "ai", label: "AI team", icon: Bot,
    tabs: [
      { href: "/workforce", label: "Receptionist" }, { href: "/approvals", label: "Approvals" }, { href: "/agents", label: "Agents" }, { href: "/automations", label: "Automations" },
      { href: "/settings/knowledge", label: "Knowledge" }, { href: "/settings/memory", label: "Memory" }, { href: "/settings/voice", label: "Voice" },
      { href: "/settings/automation", label: "Rules" }, { href: "/ai-activity", label: "Activity" },
    ],
  },
  { key: "website", label: "Website", icon: Globe, tabs: [{ href: "/website", label: "Website" }, { href: "/builder", label: "App Builder" }] },
  {
    key: "business", label: "Business", icon: Hammer,
    tabs: [{ href: "/business", label: "Guided setup" }, { href: "/setup", label: "Setup" }, { href: "/business/blueprint", label: "Blueprint" }, { href: "/business/map", label: "Business map" }],
  },
  {
    key: "settings", label: "Settings", icon: Settings2,
    tabs: [
      { href: "/settings/team", label: "Team" }, { href: "/settings/billing", label: "Billing" }, { href: "/settings/integrations", label: "Integrations" },
      { href: "/settings/compliance", label: "Compliance" }, { href: "/settings/account", label: "Account" },
    ],
  },
];

/** The areas this tenant sees: each area keeps only the tabs whose module (if any) is enabled, and an area left with no tab is dropped. */
export function visibleAreas(modules: string[]): NavArea[] {
  const on = new Set(modules);
  return AREAS.map((a) => ({ ...a, tabs: a.tabs.filter((t) => (!t.module || on.has(t.module)) && !(t.hideWithModule && on.has(t.hideWithModule))) })).filter((a) => a.tabs.length > 0);
}

/** The area and tab a path belongs to: the most specific tab href that the path equals or sits under. */
export function locate(pathname: string | null, areas: NavArea[]): { area: NavArea; tab: AreaTab } | null {
  if (!pathname) return null;
  const path = pathname.startsWith("/medical-tourism/patients/") ? "/medical-tourism/pipeline" : pathname; // a patient's case sits under Patients
  let best: { area: NavArea; tab: AreaTab } | null = null;
  for (const area of areas) {
    for (const tab of area.tabs) {
      if ((path === tab.href || path.startsWith(`${tab.href}/`)) && (!best || tab.href.length > best.tab.href.length)) best = { area, tab };
    }
  }
  return best;
}
