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
import { AREAS, PILOT_AREAS, locate, type NavArea } from "./navAreas";

type NavItem = { href: string; label: string; icon: any };

function groupOf(areas: NavArea[], href: string): string {
  return areas.find((a) => a.tabs.some((t) => t.href === href))?.label ?? "Pages";
}

export type Crumb = { label: string; href?: string };

function NavLink({ item, active, badge }: { item: NavItem; active: boolean; badge?: number }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`group relative flex items-center gap-3 rounded-xl px-3 py-2 text-[14px] transition-all duration-300 ease-apple ${
        active ? "bg-black/[0.07] font-medium text-foreground" : "text-muted hover:bg-black/[0.04] hover:text-foreground"
      }`}
    >
      <Icon className={`h-[18px] w-[18px] shrink-0 ${active ? "text-accent" : "text-muted-foreground group-hover:text-foreground"}`} strokeWidth={1.75} />
      {item.label}
      {badge !== undefined && badge > 0 && (
        <span aria-label={`${badge} open`} className="ml-auto rounded-full bg-accent px-1.5 py-0.5 text-xs font-medium leading-none text-accent-foreground">
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
  const areas: NavArea[] = pilot ? PILOT_AREAS : AREAS;
  const navItems: NavItem[] = areas.flatMap((a) => a.tabs.map((t) => ({ href: t.href, label: t.label, icon: a.icon })));

  // Only the single most specific matching tab is "active"; its area is highlighted in the sidebar and its sibling tabs are shown above the page.
  const located = locate(pathname, areas);
  const activeHref = located?.tab.href ?? null;
  const activeItem = located ? { label: located.tab.label, icon: located.area.icon } : null;
  const activeArea = located?.area ?? null;
  const ActiveIcon = activeItem?.icon ?? null;

  useEffect(() => {
    setToken(getAccessToken());
  }, []);

  // The sidebar is a fixed overlay below the lg breakpoint — close it on every navigation.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

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
          ...(activeArea && activeArea.tabs.length > 1 ? [{ label: activeArea.label }] : []),
          { label: activeItem.label },
        ]
      : []);

  return (
    <div className="flex min-h-screen bg-background">
      {mobileNavOpen && <div aria-hidden onClick={() => setMobileNavOpen(false)} className="fixed inset-0 z-40 bg-black/30 lg:hidden" />}
      <aside
        aria-label="Main"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-border/70 bg-surface-muted/80 text-muted backdrop-blur-2xl transition-transform duration-200 ease-in-out lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-4 pb-3 pt-4">
          <Logo href={pilot ? "/medical-tourism/overview" : "/business/home"} />
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close menu"
            className="flex h-8 w-8 items-center justify-center rounded-xl text-muted hover:bg-black/[0.06] lg:hidden"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="px-3 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" strokeWidth={2} />
            <kbd aria-hidden="true" className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border px-1 text-xs font-medium text-muted-foreground">⌘K</kbd>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a page…"
              aria-label="Find a page"
              className="w-full rounded-xl border border-border bg-black/[0.04] py-1.5 pl-8 pr-10 text-[13px] text-foreground placeholder:text-muted-foreground focus:border-accent focus:bg-surface focus:outline-none"
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
            <div className="space-y-0.5 py-1">
              {areas.map((area) => (
                <NavLink
                  key={area.key}
                  item={{ href: area.tabs[0].href, label: area.label, icon: area.icon }}
                  active={area.key === activeArea?.key}
                  badge={pilot && area.key === "patients" ? openTasks : undefined}
                />
              ))}
            </div>
          )}
        </nav>
        {user && (
          <div className="border-t border-border/70 p-3">
            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left outline-none transition-colors hover:bg-black/[0.04] focus-visible:ring-2 focus-visible:ring-accent">
                <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-white">
                  {(user.full_name || user.email).trim().charAt(0).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-foreground">{user.full_name}</span>
                  <span className="block truncate text-xs text-muted-foreground">{user.email}</span>
                </span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={2} />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content side="top" align="start" sideOffset={8} className="z-[70] min-w-[14rem] rounded-3xl bg-surface-muted p-1.5 text-sm shadow-popover">
                  <DropdownMenu.Item asChild>
                    <Link href="/settings/account" className="flex cursor-pointer items-center gap-2 rounded-xl px-2.5 py-2 text-foreground outline-none data-[highlighted]:bg-surface-muted">
                      <UserCog className="h-4 w-4 text-muted-foreground" strokeWidth={2} /> Account &amp; security
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Item asChild>
                    <Link href="/settings/billing" className="flex cursor-pointer items-center gap-2 rounded-xl px-2.5 py-2 text-foreground outline-none data-[highlighted]:bg-surface-muted">
                      <CreditCard className="h-4 w-4 text-muted-foreground" strokeWidth={2} /> Billing
                    </Link>
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="my-1 h-px bg-border" />
                  <DropdownMenu.Item onSelect={() => void signOut()} className="flex cursor-pointer items-center gap-2 rounded-xl px-2.5 py-2 text-danger outline-none data-[highlighted]:bg-danger/10">
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
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-foreground lg:hidden"
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
              className="hidden items-center gap-2 rounded-3xl bg-surface-muted px-2.5 py-1.5 text-xs text-muted shadow-sm transition-colors hover:border-border-strong hover:text-foreground sm:flex"
            >
              <Search className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
              Jump to…
              <kbd className="rounded border border-border bg-surface-muted px-1 text-xs font-medium text-muted-foreground">⌘K</kbd>
            </button>
            <NotificationBell token={token} />
          </div>
        </header>
        <main key={pathname} className="klaros-page-in min-w-0 flex-1 overflow-x-clip">
          {activeArea && activeArea.tabs.length > 1 && (
            <nav aria-label={`${activeArea.label} sections`} className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-5 pt-5 sm:px-8">
              {activeArea.tabs.map((t) => {
                const on = t.href === activeHref;
                return (
                  <Link key={t.href} href={t.href} aria-current={on ? "page" : undefined} className={`shrink-0 rounded-full px-3.5 py-1.5 text-[13px] transition-colors ${on ? "bg-foreground font-medium text-background" : "text-muted hover:bg-black/[0.05] hover:text-foreground"}`}>
                    {t.label}
                    {pilot && t.href === "/medical-tourism/tasks" && openTasks > 0 && <span aria-label={`${openTasks} open`} className="ml-1.5 rounded-full bg-accent px-1.5 py-0.5 text-xs font-medium leading-none text-accent-foreground">{openTasks}</span>}
                  </Link>
                );
              })}
            </nav>
          )}
          {children}
        </main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        items={navItems.map((i) => ({ href: i.href, label: i.label, icon: i.icon, group: groupOf(areas, i.href) }))}
      />
    </div>
  );
}
