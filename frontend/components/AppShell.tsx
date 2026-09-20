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
} from "lucide-react";
import { UserResponse, logout as logoutRequest } from "@/lib/api";
import NotificationBell from "./NotificationBell";

const NAV_SECTIONS: { label: string; items: { href: string; label: string; icon: any }[] }[] = [
  {
    label: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/morning-brief", label: "Morning Brief", icon: Sunrise },
    ],
  },
  {
    label: "CRM",
    items: [
      { href: "/leads", label: "Leads", icon: Users },
      { href: "/customers", label: "Customers", icon: Users },
      { href: "/calendar", label: "Calendar", icon: Calendar },
    ],
  },
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
      { href: "/events", label: "Events", icon: Radio },
      { href: "/automations", label: "Automations", icon: Workflow },
      { href: "/approvals", label: "Approvals", icon: CheckSquare },
      { href: "/ai-activity", label: "AI Activity", icon: BrainCircuit },
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

export default function AppShell({
  user,
  children,
}: {
  user: UserResponse | null;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [expandedLoaded, setExpandedLoaded] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Only the single most specific matching nav item is "active" — without
  // this, a page like /finance/ar matches both the "Finance" item (href
  // /finance) and the "AR" item (href /finance/ar) under a plain
  // startsWith check, highlighting both at once.
  const activeHref = (() => {
    if (!pathname) return null;
    let best: string | null = null;
    for (const section of NAV_SECTIONS) {
      for (const item of section.items) {
        const matches = pathname === item.href || pathname.startsWith(`${item.href}/`);
        if (matches && (!best || item.href.length > best.length)) {
          best = item.href;
        }
      }
    }
    return best;
  })();

  const activeSectionLabel = NAV_SECTIONS.find((s) => s.items.some((i) => i.href === activeHref))?.label ?? null;
  const activeItem = NAV_SECTIONS.flatMap((s) => s.items).find((i) => i.href === activeHref) ?? null;
  const ActiveIcon = activeItem?.icon ?? null;

  useEffect(() => {
    setToken(sessionStorage.getItem("klaros_access_token"));
  }, []);

  // The sidebar is a fixed-position overlay below the lg breakpoint (see
  // the <aside> below) — close it on every navigation so it doesn't stay
  // open covering the new page's content.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  // Sections collapse by default — 8 sections / ~35 links at once is a lot
  // to scan. Only the section containing the current page starts open. The
  // user's own expand/collapse choices are then remembered across
  // navigation and reloads via localStorage (per-browser, not synced).
  useEffect(() => {
    let stored: Record<string, boolean> = {};
    try {
      stored = JSON.parse(localStorage.getItem("klaros_nav_expanded") ?? "{}");
    } catch {
      stored = {};
    }
    const initial: Record<string, boolean> = {};
    for (const section of NAV_SECTIONS) {
      initial[section.label] = stored[section.label] ?? section.label === activeSectionLabel;
    }
    setExpanded(initial);
    setExpandedLoaded(true);
    // Only ever seed from the section active on first mount — later route
    // changes shouldn't silently re-expand a section the user collapsed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSection(label: string) {
    setExpanded((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      try {
        localStorage.setItem("klaros_nav_expanded", JSON.stringify(next));
      } catch {
        // best-effort only
      }
      return next;
    });
  }

  const filteredSections = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return NAV_SECTIONS.map((section) => ({
      ...section,
      items: section.items.filter((item) => item.label.toLowerCase().includes(q)),
    })).filter((section) => section.items.length > 0);
  }, [query]);

  async function signOut() {
    const currentToken = sessionStorage.getItem("klaros_access_token");
    if (currentToken) {
      // Real revocation (Phase 12) — bumps the user's token_version
      // server-side so this token (and any other still-outstanding one)
      // stops working immediately, not just locally. Best-effort: even if
      // this fails (e.g. already expired), still clear local state below.
      await logoutRequest(currentToken).catch(() => {});
    }
    sessionStorage.removeItem("klaros_access_token");
    sessionStorage.removeItem("klaros_refresh_token");
    router.push("/login");
  }

  return (
    <div className="flex min-h-screen bg-background">
      {mobileNavOpen && (
        <div
          aria-hidden
          onClick={() => setMobileNavOpen(false)}
          className="fixed inset-0 z-40 bg-black/30 lg:hidden"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-60 shrink-0 flex-col border-r border-border bg-surface transition-transform duration-200 ease-in-out lg:static lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-5">
          <Link href="/dashboard" className="font-display text-lg italic text-foreground">
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
        <div className="border-b border-border px-3 py-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" strokeWidth={2} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a page..."
              className="w-full rounded-lg border border-border-strong bg-surface py-1.5 pl-8 pr-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
            />
          </div>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-3">
          {(filteredSections ?? NAV_SECTIONS).map((section) => {
            const isOpen = filteredSections ? true : (expandedLoaded ? expanded[section.label] : section.label === activeSectionLabel);
            return (
              <div key={section.label} className="py-1">
                {filteredSections ? (
                  <div className="klaros-label px-2 pb-1.5">{section.label}</div>
                ) : (
                  <button
                    type="button"
                    onClick={() => toggleSection(section.label)}
                    className="flex w-full items-center justify-between rounded-lg px-2 py-1 text-left"
                  >
                    <span className="klaros-label">{section.label}</span>
                    <ChevronDown
                      className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`}
                      strokeWidth={2}
                    />
                  </button>
                )}
                {isOpen && (
                  <div className="space-y-0.5">
                    {section.items.map((item) => {
                      const active = item.href === activeHref;
                      const Icon = item.icon;
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={`flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                            active
                              ? "bg-accent-soft font-medium text-accent"
                              : "text-muted hover:bg-surface-muted hover:text-foreground"
                          }`}
                        >
                          <Icon className="h-4 w-4 shrink-0" strokeWidth={2} />
                          {item.label}
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
          {filteredSections && filteredSections.length === 0 && (
            <p className="px-2 py-4 text-center text-xs text-muted-foreground">No pages match &ldquo;{query}&rdquo;.</p>
          )}
        </nav>
        {user && (
          <div className="border-t border-border px-5 py-4">
            <div className="text-xs font-medium text-foreground">{user.full_name}</div>
            <div className="truncate text-xs text-muted">{user.email}</div>
            <button onClick={signOut} className="mt-2 text-xs font-medium text-accent hover:text-accent-hover">
              Sign out
            </button>
          </div>
        )}
      </aside>
      <div className="relative flex flex-1 flex-col overflow-x-auto">
        {/* A faint, fixed accent glow behind just the header — subtle enough
            not to compete with data below, but enough to give the header's
            glass something real to blur. Deliberately not repeated over the
            rest of the page: dense tables/forms need a flat, high-contrast
            background to stay legible, which is why only chrome (this
            header, modals) gets the glass treatment, never data views. */}
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 right-0 h-64 w-64 -z-10 rounded-full opacity-[0.08] blur-3xl"
          style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
        />
        <header className="klaros-glass sticky top-0 z-30 flex items-center gap-3 px-4 py-2.5 sm:px-6">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open menu"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground lg:hidden"
          >
            <Menu className="h-5 w-5" strokeWidth={2} />
          </button>
          {activeItem && (
            <div className="flex min-w-0 items-center gap-2 text-sm">
              {activeSectionLabel && activeSectionLabel !== activeItem.label && (
                <>
                  <span className="hidden text-muted-foreground sm:inline">{activeSectionLabel}</span>
                  <span className="hidden text-border-strong sm:inline">/</span>
                </>
              )}
              {ActiveIcon && <ActiveIcon className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={2} />}
              <span className="truncate font-medium text-foreground">{activeItem.label}</span>
            </div>
          )}
          <div className="flex flex-1 justify-end">
            <NotificationBell token={token} />
          </div>
        </header>
        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
