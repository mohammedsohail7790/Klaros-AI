"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  OutreachCampaignDetail,
  findOutreachContacts,
  getOutreachCampaign,
  pauseOutreachCampaign,
  setOutreachContact,
  startOutreachCampaign,
  stopOutreachCampaign,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const label = (s: string) => s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ");

/**
 * A clinic outreach campaign at a glance: where it stands, how many clinics are at each stage, who needs a contact, and the controls.
 * Shadow mode only: nothing is sent in this release, and going live is a later release.
 */
export function ClinicOutreachPanel({ token, campaignId }: { token: string | null; campaignId: string }) {
  const [data, setData] = useState<OutreachCampaignDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [emails, setEmails] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setData(await getOutreachCampaign(token, campaignId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load this campaign just now.");
    }
  }, [token, campaignId]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(action: (t: string, id: string) => Promise<unknown>, after?: (r: never) => void) {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await action(token, campaignId);
      after?.(r as never);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveEmail(providerId: string) {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setRowErrors((r) => ({ ...r, [providerId]: "" }));
    try {
      await setOutreachContact(token, campaignId, providerId, (emails[providerId] ?? "").trim());
      setEmails((e) => ({ ...e, [providerId]: "" }));
      await load();
    } catch (err) {
      setRowErrors((r) => ({ ...r, [providerId]: err instanceof ApiError ? err.message : "We couldn't save that address just now." }));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <p role="alert" className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</p> : <p className="text-sm text-muted">Loading...</p>;

  const status = data.campaign.status;
  const needsYou = data.clinics.filter((c) => c.stage === "NO_EMAIL");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge status={status}>{status}</Badge>
        <Badge>{label(data.mode)}</Badge>
        <span className="text-sm text-muted">Nothing is sent in this release.</span>
      </div>

      {error && <p role="alert" className="rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</p>}
      {notice && <p className="rounded-xl border border-border bg-surface p-3 text-sm text-muted">{notice}</p>}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() =>
            run(findOutreachContacts, (r: { status: string; checked: number; found: number }) =>
              setNotice(r.status === "not_configured" ? "Contact search isn't set up yet, so only clinics' own addresses can be used." : `Checked ${r.checked} clinics and found ${r.found} contacts.`)
            )
          }
        >
          Find contacts
        </Button>
        <Button disabled={busy || status === "ACTIVE" || status === "COMPLETED"} onClick={() => run(startOutreachCampaign)}>
          Start in shadow mode
        </Button>
        <Button variant="secondary" disabled={busy || status !== "ACTIVE"} onClick={() => run(pauseOutreachCampaign)}>
          Pause
        </Button>
        <Button variant="danger" disabled={busy || status === "COMPLETED"} onClick={() => run(stopOutreachCampaign)}>
          Stop
        </Button>
        <Button variant="ghost" disabled>
          Go live: Available after the shadow week (a later release)
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Object.entries(data.funnel).map(([stage, count]) => (
          <div key={stage} data-testid={`funnel-${stage}`} className="rounded-xl border border-border bg-surface p-3">
            <div className="text-xs text-muted">{label(stage)}</div>
            <div className="text-xl font-semibold">{count}</div>
          </div>
        ))}
      </div>

      {needsYou.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Needs you</h2>
          <p className="mb-3 text-sm text-muted">We couldn&apos;t find an email for these clinics. Add one by hand, using the clinic&apos;s own domain.</p>
          <ul className="space-y-3">
            {needsYou.map((c) => (
              <li key={c.provider_id} className="flex flex-wrap items-start gap-2">
                <span className="w-48 pt-2 text-sm">{c.name}</span>
                <div>
                  <input
                    aria-label={`Email for ${c.name}`}
                    value={emails[c.provider_id] ?? ""}
                    onChange={(e) => setEmails((m) => ({ ...m, [c.provider_id]: e.target.value }))}
                    className="rounded-xl border border-border-strong bg-surface px-3 py-2 text-sm"
                  />
                  {rowErrors[c.provider_id] && <p className="mt-1 text-xs text-danger">{rowErrors[c.provider_id]}</p>}
                </div>
                <Button size="sm" variant="secondary" disabled={busy || !(emails[c.provider_id] ?? "").trim()} onClick={() => saveEmail(c.provider_id)}>
                  Save email
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.clinics.length === 0 ? (
        <EmptyState compact title="No clinics in this campaign." />
      ) : (
        <div className="klaros-table-wrap">
          <table className="klaros-table">
            <thead>
              <tr>
                <th className="px-5 py-4">Clinic</th>
                <th className="px-5 py-4">Stage</th>
                <th className="px-5 py-4">Contact</th>
              </tr>
            </thead>
            <tbody>
              {data.clinics.map((c) => (
                <tr key={c.provider_id} className="border-t border-border">
                  <td className="px-5 py-4">
                    {c.name}
                    <span className="ml-2 text-xs text-muted">{[c.city, c.country].filter(Boolean).join(", ")}</span>
                  </td>
                  <td className="px-5 py-4">
                    <Badge status={c.stage}>{label(c.stage)}</Badge>
                  </td>
                  <td className="px-5 py-4 text-muted">{c.contact_email ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
