"use client";

import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { OutreachWording } from "@/components/marketing/OutreachWording";

export default function OutreachWordingPage() {
  const { token, user } = useAuth();
  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-3xl space-y-4 px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="text-xl font-semibold">Outreach wording</h1>
        <OutreachWording token={token} />
      </div>
    </AppShell>
  );
}
