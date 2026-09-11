"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
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
  Phone,
  Plug,
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
      { href: "/marketing/reactivation", label: "Reactivation", icon: Heart },
    ],
  },
  {
    label: "Retention",
    items: [
      { href: "/retention", label: "Retention", icon: Heart },
      { href: "/retention/opportunities", label: "Opportunities", icon: TrendingUp },
      { href: "/retention/reminders", label: "Reminders", icon: AlertTriangle },
      { href: "/retention/reviews", label: "Reviews", icon: FileText },
      { href: "/retention/referrals", label: "Referrals", icon: Users },
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

  useEffect(() => {
    setToken(sessionStorage.getItem("klaros_access_token"));
  }, []);

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
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
        <div className="border-b border-border px-5 py-5">
          <Link href="/dashboard" className="font-display text-lg italic text-foreground">
            Klaros
          </Link>
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-5">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <div className="klaros-label px-2 pb-1.5">{section.label}</div>
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const active = pathname?.startsWith(item.href);
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
            </div>
          ))}
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
      <div className="flex flex-1 flex-col overflow-x-auto">
        <header className="flex items-center justify-end border-b border-border bg-surface px-6 py-2.5">
          <NotificationBell token={token} />
        </header>
        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
