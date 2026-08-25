"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, UserResponse } from "@/lib/api";

const PENDING_MODULES = [
  "Today / AI Morning Brief",
  "Needs Your Attention (exception engine)",
  "Approvals",
  "AI Handled",
  "Business Health",
];

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<UserResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = sessionStorage.getItem("klaros_access_token");
    if (!token) {
      router.push("/login");
      return;
    }
    getCurrentUser(token)
      .then(setUser)
      .catch(() => {
        sessionStorage.removeItem("klaros_access_token");
        setError("Session expired. Please sign in again.");
        router.push("/login");
      });
  }, [router]);

  if (error) return <p className="p-8 text-sm text-red-400">{error}</p>;
  if (!user) return <p className="p-8 text-sm text-neutral-400">Loading...</p>;

  return (
    <main className="min-h-screen px-8 py-10">
      <header className="mb-8 flex items-center justify-between border-b border-neutral-800 pb-4">
        <div>
          <h1 className="text-xl font-semibold">Owner Cockpit</h1>
          <p className="text-sm text-neutral-500">
            Signed in as {user.full_name} · {user.email} · role {user.role}
          </p>
        </div>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-6">
        <h2 className="mb-2 text-sm font-medium text-neutral-300">Foundation status</h2>
        <p className="text-sm text-neutral-500">
          Authentication, multi-tenancy, and RBAC are live. The modules below are not yet built —
          this cockpit will never show placeholder numbers for them.
        </p>
        <ul className="mt-4 space-y-2">
          {PENDING_MODULES.map((m) => (
            <li key={m} className="flex items-center justify-between text-sm">
              <span className="text-neutral-300">{m}</span>
              <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs text-neutral-500">
                not connected
              </span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
