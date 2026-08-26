"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { UserResponse } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/leads", label: "Leads" },
  { href: "/customers", label: "Customers" },
  { href: "/calendar", label: "Calendar" },
  { href: "/operations", label: "Operations" },
  { href: "/jobs", label: "Jobs" },
  { href: "/exceptions", label: "Exceptions" },
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

  function signOut() {
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
      <main className="flex-1 overflow-x-auto">{children}</main>
    </div>
  );
}
