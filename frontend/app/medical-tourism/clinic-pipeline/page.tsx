"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Building2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, ClinicAgreementRow, getClinicAgreements } from "@/lib/api";
import { PIPELINE_STAGES, column, countries, pipelineSummary, withGroups } from "@/lib/mtPipeline";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

/** Hospital pipeline: where each hospital stands on the way to its first referral. Read from the same derived stages as Clinic agreements. */
export default function ClinicPipelinePage() {
  const { token, user, loading: authLoading } = useAuth();
  const [rows, setRows] = useState<ClinicAgreementRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [country, setCountry] = useState("all");

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setRows((await getClinicAgreements(token)).clinics);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load the hospital pipeline.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const items = useMemo(() => (rows ? withGroups(rows) : []), [rows]);
  const places = useMemo(() => countries(items), [items]);
  const summary = useMemo(() => pipelineSummary(items), [items]);

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-[96rem] px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader title="Hospital pipeline" description="Every hospital you want as a referral partner, from the first contact to being ready to receive a patient." icon={Building2} />
        {error ? (
          <p role="alert" className="text-danger">{error}</p>
        ) : authLoading || rows === null ? (
          <Skeleton />
        ) : items.length === 0 ? (
          <EmptyState title="No hospitals yet. Add one on the Providers page and it appears here." />
        ) : (
          <>
            <section aria-label="Summary" className="mb-6 rounded-3xl bg-surface-muted px-6 py-5">
              <p className="text-[17px] font-semibold text-foreground">{summary.headline}</p>
              <p className="mt-1 text-[15px] text-muted">{summary.detail}</p>
            </section>

            <div className="mb-5 flex flex-wrap items-center gap-2" role="group" aria-label="Filter by country">
              {["all", ...places].map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={country === c}
                  onClick={() => setCountry(c)}
                  className={`rounded-full border px-3.5 py-1 text-[13px] ${country === c ? "border-foreground bg-foreground text-background" : "border-border bg-background text-foreground"}`}
                >
                  {c === "all" ? "All countries" : c}
                </button>
              ))}
              <Link href="/medical-tourism/agreements" className="ml-auto text-[14px] text-accent hover:underline">Set commission terms &rsaquo;</Link>
            </div>

            <div className="flex gap-3 overflow-x-auto pb-4" role="list" aria-label="Pipeline columns">
              {PIPELINE_STAGES.map((s) => {
                const total = items.filter((r) => r.stage === s.key).length;
                const rowsHere = column(items, s.key, country);
                return (
                  <section key={s.key} role="listitem" aria-label={s.label} className="w-64 shrink-0 rounded-2xl bg-surface-muted p-3">
                    <h2 className="flex items-baseline justify-between px-1 text-[14px] font-semibold text-foreground">
                      {s.label}
                      <span className="font-normal text-muted">{total}</span>
                    </h2>
                    <p className="mb-3 min-h-[2rem] px-1 text-[12px] leading-snug text-muted">{s.hint}</p>
                    {rowsHere.length === 0 ? (
                      <p className="px-1 py-2 text-[13px] text-muted-foreground">{total > 0 ? "None in this country" : "No one here"}</p>
                    ) : (
                      <ul className="space-y-2">
                        {rowsHere.map((r) => (
                          <li key={r.provider_id} className="rounded-xl border border-border bg-background p-3">
                            <Link href="/medical-tourism/providers" className="text-[14px] font-semibold text-foreground hover:underline">{r.name}</Link>
                            <p className="mt-0.5 text-[12px] text-muted">{r.country}</p>
                            {r.next_step && <p className="mt-1.5 text-[12px] leading-snug text-foreground">{r.next_step}</p>}
                            <p className="mt-1.5 flex flex-wrap gap-1.5 text-[11px]">
                              {r.group && <span className="rounded-full bg-accent/10 px-2 py-0.5 text-accent" title="Several clinics share this name; the group may publish one contact for all">{r.group} group</span>}
                              {r.referrals_sent > 0 && <span className="rounded-full bg-surface-muted px-2 py-0.5 text-muted">{r.referrals_sent} referral{r.referrals_sent === 1 ? "" : "s"} sent</span>}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                );
              })}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
