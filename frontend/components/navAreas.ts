import {
  Home, Users, Wrench, DollarSign, Megaphone, Bot, Globe, Hammer, Stethoscope, Settings2,
} from "lucide-react";

export type AreaTab = { href: string; label: string };
export type NavArea = { key: string; label: string; icon: any; tabs: AreaTab[] };

/**
 * The product's navigation: a handful of areas, each with tabs. Every page keeps its own URL; an area only groups them, so no bookmark breaks.
 * A page belongs to the area whose most specific tab href it sits under (so /retention/reviews is under "Customer care" in Growth).
 * Developer-only pages such as /events are deliberately not listed: they stay reachable by URL.
 */
export const AREAS: NavArea[] = [
  { key: "home", label: "Home", icon: Home, tabs: [{ href: "/business/home", label: "Home" }] },
  {
    key: "people", label: "People", icon: Users,
    tabs: [{ href: "/leads", label: "Leads" }, { href: "/customers", label: "Customers" }, { href: "/calendar", label: "Calendar" }],
  },
  {
    key: "work", label: "Work", icon: Wrench,
    tabs: [{ href: "/operations", label: "Overview" }, { href: "/jobs", label: "Jobs" }, { href: "/operations/workers", label: "Workers" }, { href: "/exceptions", label: "Exceptions" }],
  },
  {
    key: "money", label: "Money", icon: DollarSign,
    tabs: [
      { href: "/quotes", label: "Quotes" }, { href: "/contracts", label: "Contracts" }, { href: "/finance/invoices", label: "Invoices" },
      { href: "/finance/ar", label: "Receivables" }, { href: "/finance/cash", label: "Cash" }, { href: "/finance/profitability", label: "Profitability" }, { href: "/vendors", label: "Vendors" },
    ],
  },
  {
    key: "growth", label: "Growth", icon: Megaphone,
    tabs: [
      { href: "/marketing", label: "Overview" }, { href: "/marketing/campaigns", label: "Campaigns" }, { href: "/marketing/content", label: "Content" },
      { href: "/marketing/seo", label: "SEO" }, { href: "/marketing/outbound", label: "Outbound" }, { href: "/marketing/nurture", label: "Nurture" },
      { href: "/marketing/reactivation", label: "Reactivation" }, { href: "/retention", label: "Customer care" },
    ],
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
    tabs: [{ href: "/business", label: "Guided setup" }, { href: "/business/blueprint", label: "Blueprint" }, { href: "/business/map", label: "Business map" }],
  },
  {
    key: "medical-tourism", label: "Medical Tourism", icon: Stethoscope,
    tabs: [
      { href: "/medical-tourism/overview", label: "Today" }, { href: "/medical-tourism/pipeline", label: "Patients" }, { href: "/medical-tourism/providers", label: "Providers" },
      { href: "/medical-tourism/procedures", label: "Procedures" }, { href: "/medical-tourism/consultations", label: "Consultations" }, { href: "/medical-tourism/referrals", label: "Commissions" },
    ],
  },
  {
    key: "settings", label: "Settings", icon: Settings2,
    tabs: [
      { href: "/settings/team", label: "Team" }, { href: "/settings/billing", label: "Billing" }, { href: "/settings/integrations", label: "Integrations" },
      { href: "/settings/compliance", label: "Compliance" }, { href: "/settings/account", label: "Account" },
    ],
  },
];

/** The area and tab a path belongs to: the most specific tab href that the path equals or sits under. */
export function locate(pathname: string | null): { area: NavArea; tab: AreaTab } | null {
  if (!pathname) return null;
  let best: { area: NavArea; tab: AreaTab } | null = null;
  for (const area of AREAS) {
    for (const tab of area.tabs) {
      if ((pathname === tab.href || pathname.startsWith(`${tab.href}/`)) && (!best || tab.href.length > best.tab.href.length)) best = { area, tab };
    }
  }
  return best;
}
