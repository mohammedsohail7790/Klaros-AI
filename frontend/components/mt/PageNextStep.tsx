"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { listMedicalTourismProviders } from "@/lib/api";
import { pageNextStep, type ClinicCounts } from "@/lib/mtNextStep";

/**
 * A short "why this is quiet and what to do next" card for a referral business, shown above pages that would otherwise look empty or generic.
 * Shown only when the Medical Tourism module is on; if the clinic counts cannot be read, the wording stays true without them.
 */
export function PageNextStep({ token, path }: { token: string | null; path: string }) {
  const [counts, setCounts] = useState<ClinicCounts | null>(null);
  const wants = pageNextStep(path, null) !== null;
  useEffect(() => {
    if (!token || !wants) return;
    let cancelled = false;
    listMedicalTourismProviders(token, { limit: 100 })
      .then((r) => {
        if (cancelled) return;
        const active = r.providers.filter((p) => p.status === "ACTIVE");
        setCounts({ total: active.length, withContact: active.filter((p) => !!p.contact_email).length, verified: active.filter((p) => p.verification_status === "VERIFIED").length });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, wants]);
  const step = pageNextStep(path, counts);
  if (!step) return null;
  return (
    <aside aria-label="Next step" className="mx-auto mt-5 max-w-6xl px-5 sm:px-8">
      <div className="flex flex-col gap-3 rounded-3xl bg-surface-muted p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold text-foreground">{step.title}</div>
          <p className="mt-1 text-[14px] leading-snug text-muted">{step.body}</p>
        </div>
        <Link href={step.action.href} className="klaros-btn-primary shrink-0 self-start sm:self-center">
          {step.action.label}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </div>
    </aside>
  );
}
