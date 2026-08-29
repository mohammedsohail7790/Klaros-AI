"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { UserResponse, logout as logoutRequest } from "@/lib/api";
import NotificationBell from "./NotificationBell";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/morning-brief", label: "Morning Brief" },
  { href: "/leads", label: "Leads" },
  { href: "/customers", label: "Customers" },
  { href: "/calendar", label: "Calendar" },
  { href: "/operations", label: "Operations" },
  { href: "/jobs", label: "Jobs" },
  { href: "/exceptions", label: "Exceptions" },
  { href: "/finance", label: "Finance" },
  { href: "/finance/invoices", label: "Invoices" },
  { href: "/finance/ar", label: "AR" },
  { href: "/finance/profitability", label: "Profitability" },
  { href: "/finance/cash", label: "Cash" },
  { href: "/marketing", label: "Marketing" },
  { href: "/marketing/campaigns", label: "Campaigns" },
  { href: "/marketing/content", label: "Content" },
  { href: "/marketing/seo", label: "SEO" },
  { href: "/marketing/outbound", label: "Outbound" },
  { href: "/marketing/reactivation", label: "Reactivation" },
  { href: "/retention", label: "Retention" },
  { href: "/retention/opportunities", label: "Opportunities" },
  { href: "/retention/reminders", label: "Reminders" },
  { href: "/retention/reviews", label: "Reviews" },
  { href: "/retention/referrals", label: "Referrals" },
  { href: "/events", label: "Events" },
  { href: "/approvals", label: "Approvals" },
  { href: "/ai-activity", label: "AI Activity" },
  { href: "/settings/automation", label: "Automation Settings" },
  { href: "/settings/knowledge", label: "Knowledge Layer" },
  { href: "/settings/integrations", label: "Integrations" },
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
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-neutral-800 bg-neutral-950 px-4 py-6">
        <div className="mb-8 px-2 text-lg font-semibold">Klaros AI</div>
        <nav className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-md px-3 py-2 text-sm ${
                  active ? "bg-neutral-800 text-white" : "text-neutral-400 hover:bg-neutral-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto pt-8">
          {user && (
            <div className="px-2 text-xs text-neutral-500">
              <div>{user.full_name}</div>
              <div className="truncate">{user.email}</div>
              <button onClick={signOut} className="mt-2 text-neutral-400 underline hover:text-white">
                Sign out
              </button>
            </div>
          )}
        </div>
      </aside>
      <div className="flex flex-1 flex-col overflow-x-auto">
        <header className="flex items-center justify-end border-b border-neutral-800 bg-neutral-950 px-6 py-2">
          <NotificationBell token={token} />
        </header>
        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
