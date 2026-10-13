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
  UserCog,
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
  GitBranch,
  ClipboardList,
  Send,
  LogOut,
} from "lucide-react";
import { clearSession, getAccessToken } from "@/lib/session";
import { UserResponse, listHandoffTasks, logout as logoutRequest } from "@/lib/api";
import NotificationBell from "./NotificationBell";
import CommandPalette from "./CommandPalette";
import { Logo } from "./Logo";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { usePilotMode } from "@/lib/usePilotMode";

type NavItem = { href: string; label: string; icon: any };

// The product's spine — always visible, in the order a business is built and then run.
const PRIMARY_NAV: NavItem[] = [
  { href: "/business/home", label: "Home", icon: Home },
  { href: "/business", label: "Build", icon: Hammer },
  { href: "/business/map", label: "Business", icon: MapIcon },
  { href: "/workforce", label: "AI Workforce", icon: Headset },
  { href: "/website", label: "Website", icon: Globe },
  { href: "/builder", label: "App Builder", icon: Sparkles },
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
      { href: "/settings/account", label: "Account & security", icon: UserCog },
      { href: "/settings/compliance", label: "Compliance", icon: ShieldCheck },
    ],
  },
];


/**
 * The focused navigation for a consent-gated Medical Tourism pilot organisation (see usePilotMode): only what a clinic coordinator needs to
 * work an enquiry that arrived from the AI receptionist. Everything else stays reachable by URL and for other organisations.
 */
const PILOT_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Patients",
    items: [
      { href: "/medical-tourism/overview", label: "Today", icon: Home },
      { href: "/medical-tourism/pipeline", label: "Patients", icon: Users },
      { href: "/medical-tourism/tasks", label: "Tasks", icon: ClipboardList },
      { href: "/medical-tourism/provider-referrals", label: "Referrals", icon: Send },
      { href: "/medical-tourism/consultations", label: "Consultations", icon: CalendarCheck },
    ],
  },
  {
    label: "Clinic network",
    items: [
      { href: "/medical-tourism/providers", label: "Providers", icon: Stethoscope },
      { href: "/medical-tourism/procedures", label: "Procedures & offers", icon: FileText },
      { href: "/contracts", label: "Contracts", icon: FileSignature },
    ],
  },
  {
    label: "Money",
    items: [
      { href: "/medical-tourism/referrals", label: "Commissions", icon: Receipt },
      { href: "/medical-tourism/revenue", label: "Revenue", icon: HandCoins },
    ],
  },
  {
    label: "Business",
    items: [
      { href: "/setup", label: "Business setup", icon: Hammer },
      { href: "/website", label: "Website", icon: Globe },
      { href: "/marketing", label: "Marketing", icon: Megaphone },
      { href: "/workforce", label: "AI receptionist", icon: Headset },
    ],
  },
  {
    label: "Settings",
    items: [
      { href: "/settings/team", label: "Team", icon: UserPlus },
      { href: "/settings/account", label: "Account & security", icon: UserCog },
      { href: "/settings/compliance", label: "Licences & insurance", icon: ShieldCheck },
    ],
  },
];
const PILOT_ITEMS: NavItem[] = PILOT_GROUPS.flatMap((g) => g.items);

const ALL_ITEMS: NavItem[] = [...PRIMARY_NAV, ...WORK_NAV, ...MODULE_SECTIONS.flatMap((s) => s.items)];

function groupOf(href: string): string {
  if (PRIMARY_NAV.some((i) => i.href === href)) return "Build & run";
  if (WORK_NAV.some((i) => i.href === href)) return "Work";
  return MODULE_SECTIONS.find((s) => s.items.some((i) => i.href === href))?.label ?? PILOT_GROUPS.find((g) => g.items.some((i) => i.href === href))?.label ?? "Pages";
}

export type Crumb = { label: string; href?: string };

function NavLink({ item, active, badge }: { item: NavItem; active: boolean; badge?: number }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`group relative flex items-center gap-3 rounded-xl px-3 py-2 text-[14px] transition-all duration-300 ease-apple ${
        active ? "bg-white/[0.12] font-medium text-white" : "text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
      }`}
    >
      <Icon className={`h-[18px] w-[18px] shrink-0 ${active ? "text-accent" : "text-slate-500 group-hover:text-slate-300"}`} strokeWidth={1.75} />
      {item.label}
      {badge !== undefined && badge > 0 && (
        <span aria-label={`${badge} open`} className="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[11px] font-medium leading-none text-accent-foreground">
          {badge}
        </span>
      )}
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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pilot = usePilotMode(token);
  const [openTasks, setOpenTasks] = useState(0);
  useEffect(() => {
    if (!pilot || !token) return;
    let cancelled = false;
    listHandoffTasks(token, { status: "OPEN" })
      .then((r) => !cancelled && setOpenTasks(r.total))
      .catch(() => {}); // a badge is a convenience: never block the page on it
    return () => {
      cancelled = true;
    };
  }, [pilot, token, pathname]);
  const navItems = pilot ? PILOT_ITEMS : ALL_ITEMS;

  // Only the single most specific matching item is "active" — otherwise /finance/ar would
  // light up both "Finance" and "AR", and every /business/* page would light up "Build".
  const activeHref = (() => {
    if (!pathname) return null;
    if (pathname.startsWith("/medical-tourism/patients/")) return "/medical-tourism/pipeline";
    let best: string | null = null;
    for (const item of [...ALL_ITEMS, ...PILOT_ITEMS]) {
      const matches = pathname === item.href || pathname.startsWith(`${item.href}/`);
      if (matches && (!best || item.href.length > best.length)) best = item.href;
    }
    return best;
  })();

  const activeItem = [...ALL_ITEMS, ...PILOT_ITEMS].find((i) => i.href === activeHref) ?? null;
  const activeModuleSection = pilot ? null : MODULE_SECTIONS.find((s) => s.items.some((i) => i.href === activeHref))?.label ?? null;
  const ActiveIcon = activeItem?.icon ?? null;

  useEffect(() => {
    setToken(getAccessToken());
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
    return navItems.filter((item) => item.label.toLowerCase().includes(q));
  }, [query, navItems]);

  async function signOut() {
    const currentToken = getAccessToken();
    if (currentToken) {
      // Real revocation: bumps the user's token_version server-side. Best-effort — even if
      // this fails (e.g. already expired), still clear local state below.
      await logoutRequest(currentToken).catch(() => {});
    }
    clearSession();
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
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col klaros-dark border-r border-white/5 bg-accent-2 text-slate-300 transition-transform duration-200 ease-in-out lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-4 pb-3 pt-4">
          <Logo href={pilot ? "/medical-tourism/overview" : "/business/home"} tone="light" />
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close menu"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 lg:hidden"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="px-3 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" strokeWidth={2} />
            <kbd aria-hidden="true" className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-white/10 px-1 text-[10px] font-medium text-slate-500">⌘K</kbd>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a page…"
              aria-label="Find a page"
              className="w-full rounded-lg border border-white/10 bg-white/[0.05] py-1.5 pl-8 pr-10 text-[13px] text-slate-100 placeholder:text-slate-500 focus:border-accent focus:bg-white/[0.08] focus:outline-none"
            />
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-3" aria-label="Primary">
          {searchResults ? (
            <div className="space-y-0.5 py-1">
              {searchResults.map((item) => (
                <NavLink key={item.href} item={item} active={item.href === activeHref} />
              ))}
              {searchResults.length === 0 && <p className="px-2 py-4 text-center text-xs text-slate-500">No pages match &ldquo;{query}&rdquo;.</p>}
            </div>
          ) : (
            pilot ? (
              <>
                {PILOT_GROUPS.map((g, i) => (
                  <div key={g.label}>
                    <div className={`klaros-label !text-slate-500 px-2.5 pb-1 ${i === 0 ? "pt-2" : "pt-5"}`}>{g.label}</div>
                    <div className="space-y-0.5">
                      {g.items.map((item) => (
                        <NavLink key={item.href} item={item} active={item.href === activeHref} badge={item.href === "/medical-tourism/tasks" ? openTasks : undefined} />
                      ))}
                    </div>
                  </div>
                ))}
              </>
            ) : (
            <>
              <div className="space-y-0.5 py-1">
                {PRIMARY_NAV.map((item) => (
                  <NavLink key={item.href} item={item} active={item.href === activeHref} />
                ))}
              </div>
              <div className="klaros-label !text-slate-500 px-2.5 pb-1 pt-5">Work</div>
              <div className="space-y-0.5">
                {WORK_NAV.map((item) => (
                  <NavLink key={item.href} item={item} active={item.href === activeHref} />
                ))}
              </div>
              <div className="mt-4 border-t border-white/10 pt-3">
                <button
                  type="button"
                  onClick={toggleMore}
                  aria-expanded={moreOpen}
                  className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
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
                            <span className="klaros-label !text-slate-500">{section.label}</span>
                            <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform ${isOpen ? "rotate-180" : ""}`} strokeWidth={2} />
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
            )
          )}
        </nav>
        {user && (
          <div className="border-t border-white/10 p-3">
            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left outline-none transition-colors hover:bg-white/5 focus-visible:ring-2 focus-visible:ring-accent">
                <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-white">
                  {(user.full_name || user.email).trim().charAt(0).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-slate-100">{user.full_name}</span>
                  <span className="block truncate text-xs text-slate-500">{user.email}</span>
                </span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content side="top" align="start" sideOffset={8} className="z-[70] min-w-[14rem] rounded-xl border border-border bg-surface p-1.5 text-sm shadow-popover">
                  <DropdownMenu.Item asChild>
                    <Link href="/settings/account" className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-foreground outline-none data-[highlighted]:bg-surface-muted">
                      <UserCog className="h-4 w-4 text-muted-foreground" strokeWidth={2} /> Account &amp; security
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Item asChild>
                    <Link href="/settings/billing" className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-foreground outline-none data-[highlighted]:bg-surface-muted">
                      <CreditCard className="h-4 w-4 text-muted-foreground" strokeWidth={2} /> Billing
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="my-1 h-px bg-border" />
                  <DropdownMenu.Item onSelect={() => void signOut()} className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-danger outline-none data-[highlighted]:bg-danger/10">
                    <LogOut className="h-4 w-4" strokeWidth={2} /> Sign out
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        )}
      </aside>
      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border/70 bg-background/75 px-4 py-3 backdrop-blur-2xl backdrop-saturate-150 sm:px-8">
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
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="hidden items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-muted shadow-sm transition-colors hover:border-border-strong hover:text-foreground sm:flex"
            >
              <Search className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
              Jump to…
              <kbd className="rounded border border-border bg-surface-muted px-1 text-[10px] font-medium text-muted-foreground">⌘K</kbd>
            </button>
            <NotificationBell token={token} />
          </div>
        </header>
        <main key={pathname} className="klaros-page-in min-w-0 flex-1 overflow-x-clip">{children}</main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        items={navItems.map((i) => ({ href: i.href, label: i.label, icon: i.icon, group: groupOf(i.href) }))}
      />
    </div>
  );
}
