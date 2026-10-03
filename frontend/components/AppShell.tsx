"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Sunrise,
  Users,
  Calendar,
  Wrench,
  AlertTriangle,
  DollarSign,
  FileText,
  FileSignature,
  Receipt,
  Landmark,
  TrendingUp,
  Wallet,
  Megaphone,
  Heart,
  Radio,
  Workflow,
  CheckSquare,
  BrainCircuit,
  Settings2,
  BookOpen,
  Layers,
  Mail,
  Phone,
  Plug,
  CreditCard,
  UserPlus,
  Truck,
  ShieldCheck,
  ChevronDown,
  Search,
  Menu,
  X,
  Globe,
  Sparkles,
  Bot,
  Stethoscope,
  CalendarCheck,
  HandCoins,
  Home,
  Headset,
  Database,
  BarChart3,
  Map as MapIcon,
  Hammer,
  ChevronRight,
} from "lucide-react";
import { UserResponse, logout as logoutRequest } from "@/lib/api";
import NotificationBell from "./NotificationBell";

type NavItem = { href: string; label: string; icon: any };

// The product's spine — always visible, in the order a business is built and then run.
const PRIMARY_NAV: NavItem[] = [
  { href: "/business/home", label: "Home", icon: Home },
  { href: "/business", label: "Build", icon: Hammer },
  { href: "/business/map", label: "Business", icon: MapIcon },
  { href: "/workforce", label: "AI Workforce", icon: Headset },
  { href: "/website", label: "Website", icon: Globe },
  { href: "/business/integrations", label: "Integrations", icon: Plug },
  { href: "/business/data", label: "Data", icon: Database },
  { href: "/business/workflows", label: "Automation", icon: Workflow },
  { href: "/business/analytics", label: "Analytics", icon: BarChart3 },
];

// Day-to-day work with people.
const WORK_NAV: NavItem[] = [
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/customers", label: "Customers", icon: Users },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/morning-brief", label: "Morning Brief", icon: Sunrise },
];

const MODULE_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: "Operations",
    items: [
      { href: "/operations", label: "Operations", icon: Wrench },
      { href: "/jobs", label: "Jobs", icon: Wrench },
      { href: "/operations/workers", label: "Workers", icon: Users },
      { href: "/exceptions", label: "Exceptions", icon: AlertTriangle },
    ],
  },
  {
    label: "Finance",
    items: [
      { href: "/finance", label: "Finance", icon: DollarSign },
      { href: "/quotes", label: "Quotes", icon: FileText },
      { href: "/contracts", label: "Contracts", icon: FileSignature },
      { href: "/finance/invoices", label: "Invoices", icon: Receipt },
      { href: "/finance/ar", label: "AR", icon: Landmark },
      { href: "/finance/profitability", label: "Profitability", icon: TrendingUp },
      { href: "/finance/cash", label: "Cash", icon: Wallet },
      { href: "/vendors", label: "Vendors", icon: Truck },
    ],
  },
  {
    label: "Marketing",
    items: [
      { href: "/marketing", label: "Marketing", icon: Megaphone },
      { href: "/marketing/campaigns", label: "Campaigns", icon: Megaphone },
      { href: "/marketing/content", label: "Content", icon: FileText },
      { href: "/marketing/seo", label: "SEO", icon: TrendingUp },
      { href: "/marketing/outbound", label: "Outbound", icon: Radio },
      { href: "/marketing/nurture", label: "Nurture", icon: Mail },
      { href: "/marketing/reactivation", label: "Reactivation", icon: Heart },
    ],
  },
  {
    label: "Retention",
    items: [
      { href: "/retention", label: "Retention", icon: Heart },
      { href: "/retention/campaigns", label: "Campaigns", icon: Megaphone },
      { href: "/retention/opportunities", label: "Opportunities", icon: TrendingUp },
      { href: "/retention/risk-signals", label: "Risk & Advocacy", icon: AlertTriangle },
      { href: "/retention/reminders", label: "Reminders", icon: AlertTriangle },
      { href: "/retention/reviews", label: "Reviews", icon: FileText },
      { href: "/retention/referrals", label: "Referrals", icon: Users },
      { href: "/retention/warranties", label: "Warranties", icon: ShieldCheck },
    ],
  },
  {
    label: "AI & Automation",
    items: [
      { href: "/agents", label: "Agents", icon: Bot },
      { href: "/events", label: "Events", icon: Radio },
      { href: "/automations", label: "Automations", icon: Workflow },
      { href: "/approvals", label: "Approvals", icon: CheckSquare },
      { href: "/ai-activity", label: "AI Activity", icon: BrainCircuit },
    ],
  },
  {
    label: "Medical Tourism",
    items: [
      { href: "/medical-tourism/providers", label: "Providers", icon: Stethoscope },
      { href: "/medical-tourism/procedures", label: "Procedures", icon: FileText },
      { href: "/medical-tourism/leads", label: "Patient Leads", icon: Users },
      { href: "/medical-tourism/consultations", label: "Consultations", icon: CalendarCheck },
      { href: "/medical-tourism/referrals", label: "Referral Commissions", icon: HandCoins },
    ],
  },
  {
    label: "Settings",
    items: [
      { href: "/settings/automation", label: "Automation Settings", icon: Settings2 },
      { href: "/settings/knowledge", label: "Knowledge Layer", icon: BookOpen },
      { href: "/settings/memory", label: "Company Memory", icon: Layers },
      { href: "/settings/voice", label: "Voice Receptionist", icon: Phone },
      { href: "/settings/integrations", label: "Integrations", icon: Plug },
      { href: "/settings/billing", label: "Billing", icon: CreditCard },
      { href: "/settings/team", label: "Team", icon: UserPlus },
      { href: "/settings/compliance", label: "Compliance", icon: ShieldCheck },
    ],
  },
];


const ALL_ITEMS: NavItem[] = [...PRIMARY_NAV, ...WORK_NAV, ...MODULE_SECTIONS.flatMap((s) => s.items)];

export type Crumb = { label: string; href?: string };

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
        active ? "bg-accent-soft font-medium text-accent-hover" : "text-muted hover:bg-surface-muted hover:text-foreground"
      }`}
    >
      <Icon className="h-4 w-4 shrink-0" strokeWidth={2} />
      {item.label}
    </Link>
  );
}

export default function AppShell({
  user,
  children,
  crumbs,
  actions,
}: {
  user: UserResponse | null;
  children: React.ReactNode;
  /** Replaces the automatic "Section / Page" trail, e.g. Leads / Pat Ient on a detail page. */
  crumbs?: Crumb[];
  /** Page-level actions shown in the header, right-aligned. */
  actions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [moreOpen, setMoreOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Only the single most specific matching item is "active" — otherwise /finance/ar would
  // light up both "Finance" and "AR", and every /business/* page would light up "Build".
  const activeHref = (() => {
    if (!pathname) return null;
    let best: string | null = null;
    for (const item of ALL_ITEMS) {
      const matches = pathname === item.href || pathname.startsWith(`${item.href}/`);
      if (matches && (!best || item.href.length > best.length)) best = item.href;
    }
    return best;
  })();

  const activeItem = ALL_ITEMS.find((i) => i.href === activeHref) ?? null;
  const activeModuleSection = MODULE_SECTIONS.find((s) => s.items.some((i) => i.href === activeHref))?.label ?? null;
  const ActiveIcon = activeItem?.icon ?? null;

  useEffect(() => {
    setToken(sessionStorage.getItem("klaros_access_token"));
  }, []);

  // The sidebar is a fixed overlay below the lg breakpoint — close it on every navigation.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  // "All modules" stays closed unless the current page lives inside it; the user's own
  // choices are then remembered per browser.
  useEffect(() => {
    let stored: Record<string, boolean> = {};
    let storedMore: boolean | undefined;
    try {
      stored = JSON.parse(localStorage.getItem("klaros_nav_expanded") ?? "{}");
      const m = localStorage.getItem("klaros_nav_more");
      storedMore = m === null ? undefined : m === "1";
    } catch {
      stored = {};
    }
    const initial: Record<string, boolean> = {};
    for (const section of MODULE_SECTIONS) initial[section.label] = stored[section.label] ?? section.label === activeModuleSection;
    setExpanded(initial);
    setMoreOpen(activeModuleSection !== null || storedMore === true);
    // Seed once from the page the user landed on; later navigation must not re-open what they closed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function remember(key: string, value: string) {
    try {
      localStorage.setItem(key, value);
    } catch {
      // best-effort only
    }
  }

  function toggleMore() {
    setMoreOpen((v) => {
      remember("klaros_nav_more", v ? "0" : "1");
      return !v;
    });
  }

  function toggleSection(label: string) {
    setExpanded((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      remember("klaros_nav_expanded", JSON.stringify(next));
      return next;
    });
  }

  const searchResults = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return ALL_ITEMS.filter((item) => item.label.toLowerCase().includes(q));
  }, [query]);

  async function signOut() {
    const currentToken = sessionStorage.getItem("klaros_access_token");
    if (currentToken) {
      // Real revocation: bumps the user's token_version server-side. Best-effort — even if
      // this fails (e.g. already expired), still clear local state below.
      await logoutRequest(currentToken).catch(() => {});
    }
    sessionStorage.removeItem("klaros_access_token");
    sessionStorage.removeItem("klaros_refresh_token");
    router.push("/login");
  }

  const trail: Crumb[] =
    crumbs ??
    (activeItem
      ? [
          ...(activeModuleSection ? [{ label: activeModuleSection }] : []),
          { label: activeItem.label },
        ]
      : []);

  return (
    <div className="flex min-h-screen bg-background">
      {mobileNavOpen && <div aria-hidden onClick={() => setMobileNavOpen(false)} className="fixed inset-0 z-40 bg-black/30 lg:hidden" />}
      <aside
        aria-label="Main"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-border bg-surface transition-transform duration-200 ease-in-out lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 pb-3 pt-5">
          <Link href="/business/home" className="font-display text-xl italic text-foreground">
            Klaros AI
          </Link>
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close menu"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-surface-muted lg:hidden"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="px-3 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" strokeWidth={2} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a page…"
              aria-label="Find a page"
              className="w-full rounded-lg border border-border-strong bg-surface py-1.5 pl-8 pr-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
            />
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-3" aria-label="Primary">
          {searchResults ? (
            <div className="space-y-0.5 py-1">
              {searchResults.map((item) => (
                <NavLink key={item.href} item={item} active={item.href === activeHref} />
              ))}
              {searchResults.length === 0 && <p className="px-2 py-4 text-center text-xs text-muted-foreground">No pages match &ldquo;{query}&rdquo;.</p>}
            </div>
          ) : (
            <>
              <div className="space-y-0.5 py-1">
                {PRIMARY_NAV.map((item) => (
                  <NavLink key={item.href} item={item} active={item.href === activeHref} />
                ))}
              </div>
              <div className="klaros-label px-2.5 pb-1 pt-4">Work</div>
              <div className="space-y-0.5">
                {WORK_NAV.map((item) => (
                  <NavLink key={item.href} item={item} active={item.href === activeHref} />
                ))}
              </div>
              <div className="mt-4 border-t border-border pt-3">
                <button
                  type="button"
                  onClick={toggleMore}
                  aria-expanded={moreOpen}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm text-muted hover:bg-surface-muted hover:text-foreground"
                >
                  <span>All modules</span>
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${moreOpen ? "rotate-180" : ""}`} strokeWidth={2} />
                </button>
                {moreOpen && (
                  <div className="mt-1 space-y-1">
                    {MODULE_SECTIONS.map((section) => {
                      const isOpen = expanded[section.label] ?? false;
                      return (
                        <div key={section.label}>
                          <button
                            type="button"
                            onClick={() => toggleSection(section.label)}
                            aria-expanded={isOpen}
                            className="flex w-full items-center justify-between rounded-lg px-2.5 py-1 text-left"
                          >
                            <span className="klaros-label">{section.label}</span>
                            <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`} strokeWidth={2} />
                          </button>
                          {isOpen && (
                            <div className="space-y-0.5">
                              {section.items.map((item) => (
                                <NavLink key={item.href} item={item} active={item.href === activeHref} />
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </nav>
        {user && (
          <div className="border-t border-border px-5 py-4">
            <div className="truncate text-xs font-medium text-foreground">{user.full_name}</div>
            <div className="truncate text-xs text-muted">{user.email}</div>
            <button onClick={signOut} className="mt-2 text-xs font-medium text-accent-hover hover:underline">
              Sign out
            </button>
          </div>
        )}
      </aside>
      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="klaros-glass sticky top-0 z-30 flex items-center gap-3 px-4 py-2.5 sm:px-6">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open menu"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground lg:hidden"
          >
            <Menu className="h-5 w-5" strokeWidth={2} />
          </button>
          {trail.length > 0 && (
            <nav aria-label="Breadcrumb" className="min-w-0">
              <ol className="flex min-w-0 items-center gap-1.5 text-sm">
                {trail.map((c, i) => {
                  const last = i === trail.length - 1;
                  return (
                    <li key={`${c.label}-${i}`} className={`flex min-w-0 items-center gap-1.5 ${last ? "" : "hidden sm:flex"}`}>
                      {i === 0 && ActiveIcon && !crumbs && <ActiveIcon className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={2} />}
                      {c.href && !last ? (
                        <Link href={c.href} className="truncate text-muted hover:text-foreground">
                          {c.label}
                        </Link>
                      ) : (
                        <span aria-current={last ? "page" : undefined} className={`truncate ${last ? "font-medium text-foreground" : "text-muted-foreground"}`}>
                          {c.label}
                        </span>
                      )}
                      {!last && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-border-strong" aria-hidden="true" />}
                    </li>
                  );
                })}
              </ol>
            </nav>
          )}
          <div className="flex flex-1 items-center justify-end gap-2">
            {actions}
            <NotificationBell token={token} />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-x-clip">{children}</main>
      </div>
    </div>
  );
}
