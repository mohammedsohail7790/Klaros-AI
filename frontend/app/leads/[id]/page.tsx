"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  AIQualifyLeadAdvisory,
  ApiError,
  Campaign,
  LeadAttribution,
  Worker,
  aiQualifyLeadAdvisory,
  attributeLead,
  convertLeadAndBook,
  getLead,
  Lead,
  listCampaigns,
  listWorkers,
  qualifyLead,
  updateLead,
} from "@/lib/api";

const STATUS_OPTIONS = ["NEW", "CONTACTED", "QUALIFIED", "UNQUALIFIED", "BOOKED", "LOST", "CONVERTED"];
const ATTRIBUTION_MODELS = ["SOURCE_ONLY", "FIRST_TOUCH", "LAST_TOUCH"];

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { token, user, loading: authLoading } = useAuth();
  const [lead, setLead] = useState<Lead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [qualifying, setQualifying] = useState(false);
  const [qualifyError, setQualifyError] = useState<string | null>(null);
  const [advisory, setAdvisory] = useState<AIQualifyLeadAdvisory | null>(null);
  const [advisoryLoading, setAdvisoryLoading] = useState(false);
  const [advisoryError, setAdvisoryError] = useState<string | null>(null);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [converting, setConverting] = useState(false);
  const [convertTitle, setConvertTitle] = useState("");
  const [convertStart, setConvertStart] = useState("");
  const [convertEnd, setConvertEnd] = useState("");
  const [convertWorkerId, setConvertWorkerId] = useState("");
  const [convertError, setConvertError] = useState<string | null>(null);
  const [convertedJobId, setConvertedJobId] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [attribution, setAttribution] = useState<LeadAttribution | null>(null);
  const [attrCampaignId, setAttrCampaignId] = useState("");
  const [attrSource, setAttrSource] = useState("");
  const [attrMedium, setAttrMedium] = useState("");
  const [attrUtmSource, setAttrUtmSource] = useState("");
  const [attrUtmMedium, setAttrUtmMedium] = useState("");
  const [attrUtmCampaign, setAttrUtmCampaign] = useState("");
  const [attrModel, setAttrModel] = useState("SOURCE_ONLY");
  const [attrSaving, setAttrSaving] = useState(false);
  const [attrError, setAttrError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [leadResult, workersResult, campaignsResult] = await Promise.all([
        getLead(token, id),
        listWorkers(token),
        listCampaigns(token),
      ]);
      setLead(leadResult.lead);
      setWorkers(workersResult.workers);
      setCampaigns(campaignsResult.campaigns);
      if (!attrSource) setAttrSource(leadResult.lead.source);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this lead.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleQualify() {
    if (!token) return;
    setQualifying(true);
    setQualifyError(null);
    try {
      await qualifyLead(token, id);
      await load();
    } catch (err) {
      setQualifyError(err instanceof ApiError ? err.message : "Unable to qualify this lead. Retry.");
    } finally {
      setQualifying(false);
    }
  }

  async function handleAdvisory() {
    if (!token) return;
    setAdvisoryLoading(true);
    setAdvisoryError(null);
    try {
      const result = await aiQualifyLeadAdvisory(token, id);
      setAdvisory(result);
    } catch (err) {
      setAdvisoryError(err instanceof ApiError ? err.message : "Unable to generate an AI recommendation. Retry.");
    } finally {
      setAdvisoryLoading(false);
    }
  }

  async function handleConvert(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token || !convertTitle.trim() || !convertStart || !convertEnd) return;
    setConverting(true);
    setConvertError(null);
    try {
      const result = await convertLeadAndBook(token, {
        lead_id: id,
        title: convertTitle.trim(),
        start_time: new Date(convertStart).toISOString(),
        end_time: new Date(convertEnd).toISOString(),
        assigned_user_id: convertWorkerId || undefined,
      });
      setConvertedJobId(result.job.id as string);
      await load();
    } catch (err) {
      setConvertError(err instanceof ApiError ? err.message : "Unable to convert this lead. Retry.");
    } finally {
      setConverting(false);
    }
  }

  async function handleAttribute(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    setAttrSaving(true);
    setAttrError(null);
    try {
      const result = await attributeLead(token, {
        lead_id: id,
        campaign_id: attrCampaignId || undefined,
        source: attrSource.trim() || undefined,
        medium: attrMedium.trim() || undefined,
        utm_source: attrUtmSource.trim() || undefined,
        utm_medium: attrUtmMedium.trim() || undefined,
        utm_campaign: attrUtmCampaign.trim() || undefined,
        attribution_model: attrModel,
      });
      setAttribution(result.attribution);
    } catch (err) {
      setAttrError(err instanceof ApiError ? err.message : "Unable to save attribution. Retry.");
    } finally {
      setAttrSaving(false);
    }
  }

  async function handleStatusChange(status: string) {
    if (!token) return;
    try {
      await updateLead(token, id, { status });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update this lead.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <Link href="/leads" className="text-sm text-muted hover:underline">
          ← Back to leads
        </Link>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="mt-4 rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !lead ? (
          <p className="mt-4 text-sm text-muted">Lead not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="lg:col-span-2 space-y-6">
              <div className="rounded-lg border border-border bg-surface p-6">
                <div className="flex items-start justify-between">
                  <div>
                    <h1 className="font-display text-2xl text-foreground">{lead.name}</h1>
                    <p className="text-sm text-muted">
                      {lead.source} · {lead.email ?? "no email"} · {lead.phone ?? "no phone"}
                    </p>
                  </div>
                  <select
                    value={lead.status}
                    onChange={(e) => handleStatusChange(e.target.value)}
                    className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>

                <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <dt className="text-muted">Service requested</dt>
                    <dd>{lead.service_requested ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Location</dt>
                    <dd>{lead.location ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Urgency</dt>
                    <dd>{lead.urgency}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Estimated value</dt>
                    <dd>{lead.estimated_value != null ? `$${lead.estimated_value.toLocaleString()}` : "—"}</dd>
                  </div>
                </dl>
              </div>

              {lead.customer_id && (
                <div className="rounded-lg border border-border bg-surface p-4 text-sm">
                  Linked to customer —{" "}
                  <Link href={`/customers/${lead.customer_id}`} className="underline">
                    view Customer 360
                  </Link>
                </div>
              )}

              <div className="rounded-lg border border-border bg-surface p-4 text-sm">
                <Link
                  href={`/calendar?lead_id=${lead.id}${lead.customer_id ? `&customer_id=${lead.customer_id}` : ""}`}
                  className="rounded-md bg-surface px-3 py-1.5 font-medium text-foreground hover:bg-surface-muted"
                >
                  Book appointment
                </Link>
              </div>

              {(convertedJobId || (lead.status !== "CONVERTED" && lead.status !== "LOST")) && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-2 text-sm font-medium text-muted">Convert to job</h2>
                  <p className="mb-3 text-xs text-muted">
                    One step: match or create the customer, book the appointment, and create the job together.
                  </p>
                  {convertedJobId ? (
                    <p className="text-sm text-success">
                      Converted —{" "}
                      <Link href={`/jobs/${convertedJobId}`} className="underline">
                        view job
                      </Link>
                    </p>
                  ) : (
                    <form onSubmit={handleConvert} className="space-y-2">
                      <input
                        value={convertTitle}
                        onChange={(e) => setConvertTitle(e.target.value)}
                        placeholder={lead.service_requested ?? "Job title"}
                        required
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                      <div className="flex gap-2">
                        <input
                          type="datetime-local"
                          value={convertStart}
                          onChange={(e) => setConvertStart(e.target.value)}
                          required
                          className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                        />
                        <input
                          type="datetime-local"
                          value={convertEnd}
                          onChange={(e) => setConvertEnd(e.target.value)}
                          required
                          className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                        />
                      </div>
                      <select
                        value={convertWorkerId}
                        onChange={(e) => setConvertWorkerId(e.target.value)}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      >
                        <option value="">Assign worker (optional)</option>
                        {workers.map((w) => (
                          <option key={w.id} value={w.id}>
                            {w.name}
                          </option>
                        ))}
                      </select>
                      <button
                        type="submit"
                        disabled={converting || !convertTitle.trim() || !convertStart || !convertEnd}
                        className="klaros-btn-primary w-full disabled:opacity-50"
                      >
                        {converting ? "Converting..." : "Convert to job"}
                      </button>
                      {convertError && <p className="text-xs text-danger">{convertError}</p>}
                    </form>
                  )}
                </div>
              )}
            </section>

            <section className="space-y-4">
              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">Qualification</h2>
                <p className="text-sm text-muted">
                  Status: <span className="text-foreground">{lead.qualification_status}</span>
                </p>
                {lead.lead_score != null && (
                  <p className="mt-1 text-2xl font-semibold">{lead.lead_score}/100</p>
                )}
                {lead.score_reason && (
                  <p className="mt-2 text-xs text-muted">{lead.score_reason}</p>
                )}
                <button
                  onClick={handleQualify}
                  disabled={qualifying}
                  className="mt-4 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {qualifying ? "Qualifying..." : "Re-run qualification"}
                </button>
                {qualifyError && <p className="mt-2 text-xs text-danger">{qualifyError}</p>}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">AI recommendation</h2>
                <p className="text-xs text-muted-foreground">
                  Advisory only — never applied automatically. Review it, then use &ldquo;Re-run qualification&rdquo;
                  above or edit the lead directly to act on it.
                </p>
                {advisory && (
                  advisory.available ? (
                    <div className="mt-3 space-y-2 text-sm">
                      {advisory.qualification_score != null && (
                        <p className="text-2xl font-semibold">{advisory.qualification_score}/100</p>
                      )}
                      <dl className="grid grid-cols-2 gap-2 text-xs">
                        {advisory.intent && (
                          <div>
                            <dt className="text-muted">Intent</dt>
                            <dd>{advisory.intent}</dd>
                          </div>
                        )}
                        {advisory.urgency && (
                          <div>
                            <dt className="text-muted">Urgency</dt>
                            <dd>{advisory.urgency}</dd>
                          </div>
                        )}
                        {advisory.buying_signal && (
                          <div>
                            <dt className="text-muted">Buying signal</dt>
                            <dd>{advisory.buying_signal}</dd>
                          </div>
                        )}
                      </dl>
                      {advisory.summary && <p className="text-muted">{advisory.summary}</p>}
                      {advisory.recommended_next_action && (
                        <p className="rounded-md border border-border-strong bg-surface-muted p-2 text-xs">
                          Recommended: {advisory.recommended_next_action}
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="mt-3 text-sm text-muted">
                      {advisory.unavailable_reason ?? "No AI provider configured — nothing was fabricated."}
                    </p>
                  )
                )}
                <button
                  onClick={handleAdvisory}
                  disabled={advisoryLoading}
                  className="mt-4 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {advisoryLoading ? "Generating..." : "Generate AI recommendation"}
                </button>
                {advisoryError && <p className="mt-2 text-xs text-danger">{advisoryError}</p>}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">Marketing attribution</h2>
                <p className="mb-3 text-xs text-muted-foreground">
                  Record where this lead really came from — campaign, source/medium, UTM params — for campaign
                  performance and CAC reporting. Only one attribution is kept per lead: with SOURCE_ONLY or
                  FIRST_TOUCH, saving again never overwrites an existing claim; LAST_TOUCH re-attributes to
                  whatever you save most recently.
                </p>
                {attribution && (
                  <div className="mb-3 rounded-md border border-success/20 bg-success/[0.06] p-2 text-xs text-success">
                    Saved — {attribution.source ?? "no source"} / {attribution.medium ?? "no medium"} (
                    {attribution.attribution_model})
                    {attribution.campaign_id &&
                      ` · campaign: ${campaigns.find((c) => c.id === attribution.campaign_id)?.name ?? attribution.campaign_id}`}
                  </div>
                )}
                <form onSubmit={handleAttribute} className="space-y-2 text-sm">
                  <div>
                    <label className="block text-xs text-muted">Campaign (optional)</label>
                    <select
                      value={attrCampaignId}
                      onChange={(e) => setAttrCampaignId(e.target.value)}
                      className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    >
                      <option value="">No campaign</option>
                      {campaigns.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex gap-2">
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Source</label>
                      <input
                        value={attrSource}
                        onChange={(e) => setAttrSource(e.target.value)}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Medium</label>
                      <input
                        value={attrMedium}
                        onChange={(e) => setAttrMedium(e.target.value)}
                        placeholder="e.g. cpc, organic, referral"
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <input
                      value={attrUtmSource}
                      onChange={(e) => setAttrUtmSource(e.target.value)}
                      placeholder="utm_source"
                      className="w-1/3 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-xs"
                    />
                    <input
                      value={attrUtmMedium}
                      onChange={(e) => setAttrUtmMedium(e.target.value)}
                      placeholder="utm_medium"
                      className="w-1/3 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-xs"
                    />
                    <input
                      value={attrUtmCampaign}
                      onChange={(e) => setAttrUtmCampaign(e.target.value)}
                      placeholder="utm_campaign"
                      className="w-1/3 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-xs"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-muted">Attribution model</label>
                    <select
                      value={attrModel}
                      onChange={(e) => setAttrModel(e.target.value)}
                      className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    >
                      {ATTRIBUTION_MODELS.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="submit"
                    disabled={attrSaving}
                    className="w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    {attrSaving ? "Saving..." : "Save attribution"}
                  </button>
                  {attrError && <p className="text-xs text-danger">{attrError}</p>}
                </form>
              </div>
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}
