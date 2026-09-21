"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  Lead,
  NurtureEnrollmentRow,
  NurtureSequenceRow,
  createNurtureSequence,
  enrollLeadInNurture,
  executeDueNurtureActivities,
  findStaleLeadCandidates,
  listNurtureEnrollments,
  listNurtureSequences,
  searchLeads,
} from "@/lib/api";

const TRIGGER_TYPES = ["STALE_LEAD", "UNBOOKED_QUALIFIED", "CUSTOM"];

export default function NurturePage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [sequences, setSequences] = useState<NurtureSequenceRow[]>([]);
  const [enrollments, setEnrollments] = useState<NurtureEnrollmentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [triggerType, setTriggerType] = useState(TRIGGER_TYPES[0]);
  const [submitting, setSubmitting] = useState(false);

  const [candidateIds, setCandidateIds] = useState<string[] | null>(null);
  const [findingCandidates, setFindingCandidates] = useState(false);

  const [enrollingSequenceId, setEnrollingSequenceId] = useState<string | null>(null);
  const [leadQuery, setLeadQuery] = useState("");
  const [leadResults, setLeadResults] = useState<Lead[]>([]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [sequencesResult, enrollmentsResult] = await Promise.all([
        listNurtureSequences(token),
        listNurtureEnrollments(token),
      ]);
      setSequences(sequencesResult.sequences);
      setEnrollments(enrollmentsResult.enrollments);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load nurture sequences.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!token || !leadQuery) {
      setLeadResults([]);
      return;
    }
    const handle = setTimeout(() => {
      searchLeads(token, { q: leadQuery, limit: 5 })
        .then((r) => setLeadResults(r.leads))
        .catch(() => setLeadResults([]));
    }, 300);
    return () => clearTimeout(handle);
  }, [leadQuery, token]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createNurtureSequence(token, name.trim(), triggerType);
      setName("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create sequence.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleFindCandidates() {
    if (!token) return;
    setFindingCandidates(true);
    setError(null);
    try {
      const result = await findStaleLeadCandidates(token);
      setCandidateIds(result.lead_ids);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to find stale lead candidates.");
    } finally {
      setFindingCandidates(false);
    }
  }

  async function handleEnroll(leadId: string, leadName: string) {
    if (!token || !enrollingSequenceId) return;
    setBusy(true);
    setError(null);
    try {
      await enrollLeadInNurture(token, enrollingSequenceId, leadId);
      toast.success(`${leadName} enrolled.`);
      setEnrollingSequenceId(null);
      setLeadQuery("");
      setLeadResults([]);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to enroll lead.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExecuteDue() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const result = await executeDueNurtureActivities(token);
      toast.success(`${result.executed_activity_ids.length} activity(ies) executed.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to execute due activities.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Lead Nurture</h1>
          <div className="flex gap-2">
            <button
              disabled={findingCandidates}
              onClick={handleFindCandidates}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {findingCandidates ? "Finding..." : "Find stale leads"}
            </button>
            <button
              disabled={busy}
              onClick={handleExecuteDue}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              Execute due activities
            </button>
            <button onClick={() => setShowCreate((v) => !v)} className="klaros-btn-primary">
              New sequence
            </button>
          </div>
        </div>

        <p className="mb-4 text-xs text-muted">
          Automated re-engagement for leads that have gone stale or qualified leads that never booked. Enrolling
          schedules a real activity; sending only ever reaches the internal test communication provider until a
          real one is connected.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">{error}</div>
        )}
        {candidateIds && (
          <div className="mb-4 rounded-md border border-border bg-surface p-3 text-sm">
            {candidateIds.length === 0 ? (
              "No stale lead candidates found (no status change in 30+ days)."
            ) : (
              <>
                {candidateIds.length} stale lead(s) found:{" "}
                {candidateIds.map((id, i) => (
                  <span key={id}>
                    {i > 0 && ", "}
                    <Link href={`/leads/${id}`} className="underline">
                      view
                    </Link>
                  </span>
                ))}
              </>
            )}
          </div>
        )}

        {showCreate && (
          <form onSubmit={handleCreate} className="mb-6 flex flex-wrap items-end gap-2 rounded-lg border border-border p-4">
            <div>
              <label className="block text-xs text-muted">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted">Trigger</label>
              <select
                value={triggerType}
                onChange={(e) => setTriggerType(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              >
                {TRIGGER_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {submitting ? "Creating..." : "Create"}
            </button>
          </form>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : sequences.length === 0 ? (
          <EmptyState icon={Mail} title="No nurture sequences yet." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Trigger</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Enrollments</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {sequences.map((s) => (
                  <Fragment key={s.id}>
                    <tr className="border-t border-border">
                      <td className="px-4 py-2">{s.name}</td>
                      <td className="px-4 py-2 text-muted">{s.trigger_type}</td>
                      <td className="px-4 py-2">
                        <Badge status={s.status}>{s.status}</Badge>
                      </td>
                      <td className="px-4 py-2 text-muted">
                        {enrollments.filter((e) => e.sequence_id === s.id).length}
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => {
                            setEnrollingSequenceId(enrollingSequenceId === s.id ? null : s.id);
                            setLeadQuery("");
                            setLeadResults([]);
                          }}
                          className="text-xs underline text-muted hover:text-foreground"
                        >
                          Enroll lead
                        </button>
                      </td>
                    </tr>
                    {enrollingSequenceId === s.id && (
                      <tr className="border-t border-border bg-surface-muted">
                        <td colSpan={5} className="px-4 py-3">
                          <div className="relative max-w-sm">
                            <input
                              value={leadQuery}
                              onChange={(e) => setLeadQuery(e.target.value)}
                              placeholder="Search lead by name/email"
                              disabled={busy}
                              className="w-full rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm disabled:opacity-50"
                            />
                            {leadResults.length > 0 && (
                              <ul className="absolute z-10 mt-1 w-full rounded-md border border-border bg-surface text-sm shadow-popover">
                                {leadResults.map((lead) => (
                                  <li
                                    key={lead.id}
                                    onClick={() => handleEnroll(lead.id, lead.name)}
                                    className="cursor-pointer px-3 py-2 hover:bg-surface-muted"
                                  >
                                    {lead.name} {lead.email && `(${lead.email})`}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
