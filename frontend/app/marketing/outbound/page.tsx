"use client";

import { useCallback, useEffect, useState } from "react";
import { Contact, List } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  OutboundContactRow,
  OutboundListRow,
  OutboundSequenceRow,
  addOutboundContact,
  addOutboundStep,
  createOutboundList,
  createOutboundSequence,
  enrollOutboundContact,
  executeDueOutboundActivities,
  listOutboundContacts,
  listOutboundLists,
  listOutboundSequences,
} from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

export default function OutboundPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [lists, setLists] = useState<OutboundListRow[]>([]);
  const [contacts, setContacts] = useState<OutboundContactRow[]>([]);
  const [sequences, setSequences] = useState<OutboundSequenceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listName, setListName] = useState("");
  const [selectedList, setSelectedList] = useState<string>("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactCompany, setContactCompany] = useState("");
  const [busy, setBusy] = useState(false);

  const [sequenceName, setSequenceName] = useState("");
  const [stepDayOffset, setStepDayOffset] = useState("0");
  const [stepChannel, setStepChannel] = useState("EMAIL");
  const [stepSubject, setStepSubject] = useState("");
  const [addingStepSequenceId, setAddingStepSequenceId] = useState<string | null>(null);
  const [enrollingSequenceId, setEnrollingSequenceId] = useState<string | null>(null);
  const [enrollContactId, setEnrollContactId] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [listsResult, contactsResult, sequencesResult] = await Promise.all([
        listOutboundLists(token),
        listOutboundContacts(token),
        listOutboundSequences(token),
      ]);
      setLists(listsResult.lists);
      setContacts(contactsResult.contacts);
      setSequences(sequencesResult.sequences);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load outbound data.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreateList(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !listName.trim()) return;
    setBusy(true);
    try {
      await createOutboundList(token, listName.trim());
      setListName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create list.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddContact(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !selectedList || !contactEmail.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addOutboundContact(token, { list_id: selectedList, company: contactCompany || undefined, email: contactEmail.trim() });
      setContactEmail("");
      setContactCompany("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add contact.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateSequence(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !sequenceName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createOutboundSequence(token, sequenceName.trim());
      setSequenceName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create sequence.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddStep(sequenceId: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await addOutboundStep(token, sequenceId, {
        day_offset: Number(stepDayOffset),
        channel: stepChannel,
        subject: stepSubject.trim() || undefined,
      });
      toast.success("Step added.");
      setAddingStepSequenceId(null);
      setStepDayOffset("0");
      setStepSubject("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to add step.");
    } finally {
      setBusy(false);
    }
  }

  async function handleEnroll(sequenceId: string) {
    if (!token || !enrollContactId) return;
    setBusy(true);
    setError(null);
    try {
      await enrollOutboundContact(token, sequenceId, enrollContactId);
      toast.success("Contact enrolled.");
      setEnrollingSequenceId(null);
      setEnrollContactId("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to enroll contact.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExecuteDue() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const result = await executeDueOutboundActivities(token);
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
          <h1 className="font-display text-2xl text-foreground">Outbound &amp; List Building</h1>
          <button
            disabled={busy}
            onClick={handleExecuteDue}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
          >
            Execute due activities
          </button>
        </div>

        {error && <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>}

        <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">Lists</h2>
            <form onSubmit={handleCreateList} className="mb-3 flex gap-2">
              <input value={listName} onChange={(e) => setListName(e.target.value)} placeholder="List name" className="flex-1 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
              <button type="submit" disabled={busy || !listName.trim()} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
                Create
              </button>
            </form>
            {authLoading || loading ? (
              <Skeleton />
            ) : lists.length === 0 ? (
              <EmptyState icon={List} title="No lists yet." compact />
            ) : (
              <ul className="space-y-1 text-sm">
                {lists.map((l) => (
                  <li key={l.id} className="text-muted">{l.name}</li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
            <h2 className="mb-3 text-sm font-medium text-muted">Add contact</h2>
            <form onSubmit={handleAddContact} className="space-y-2">
              <select value={selectedList} onChange={(e) => setSelectedList(e.target.value)} className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm">
                <option value="">Select a list...</option>
                {lists.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
              <input value={contactCompany} onChange={(e) => setContactCompany(e.target.value)} placeholder="Company (optional)" className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
              <input value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} placeholder="Email" className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm" />
              <button type="submit" disabled={busy || !selectedList || !contactEmail.trim()} className="w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50">
                Add contact
              </button>
            </form>
          </div>
        </div>

        <h2 className="mb-3 text-sm font-medium text-muted">Contacts ({contacts.length})</h2>
        {contacts.length === 0 ? (
          <EmptyState icon={Contact} title="No contacts yet." />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Company</th>
                  <th className="px-4 py-2">Email</th>
                  <th className="px-4 py-2">Source</th>
                  <th className="px-4 py-2">Enrichment</th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-4 py-2">{c.company || "—"}</td>
                    <td className="px-4 py-2 text-muted">{c.email}</td>
                    <td className="px-4 py-2 text-muted">{c.source}</td>
                    <td className="px-4 py-2 text-muted">{c.enrichment_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h2 className="mb-3 mt-10 text-sm font-medium text-muted">Sequences</h2>
        <form onSubmit={handleCreateSequence} className="mb-3 flex gap-2">
          <input
            value={sequenceName}
            onChange={(e) => setSequenceName(e.target.value)}
            placeholder="Sequence name"
            className="flex-1 max-w-sm rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
          />
          <button
            type="submit"
            disabled={busy || !sequenceName.trim()}
            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
          >
            Create
          </button>
        </form>
        {sequences.length === 0 ? (
          <EmptyState icon={List} title="No sequences yet." compact />
        ) : (
          <ul className="space-y-2">
            {sequences.map((s) => (
              <li key={s.id} className="rounded-md border border-border bg-surface p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span>
                    {s.name} <Badge status={s.status}>{s.status}</Badge>
                  </span>
                  <div className="flex gap-3">
                    <button
                      onClick={() => {
                        setAddingStepSequenceId(addingStepSequenceId === s.id ? null : s.id);
                        setEnrollingSequenceId(null);
                      }}
                      className="text-xs underline text-muted hover:text-foreground"
                    >
                      Add step
                    </button>
                    <button
                      onClick={() => {
                        setEnrollingSequenceId(enrollingSequenceId === s.id ? null : s.id);
                        setAddingStepSequenceId(null);
                      }}
                      className="text-xs underline text-muted hover:text-foreground"
                    >
                      Enroll contact
                    </button>
                  </div>
                </div>
                {addingStepSequenceId === s.id && (
                  <div className="mt-2 flex flex-wrap items-end gap-2 border-t border-border pt-2">
                    <div>
                      <label className="block text-xs text-muted">Day offset</label>
                      <input
                        type="number"
                        value={stepDayOffset}
                        onChange={(e) => setStepDayOffset(e.target.value)}
                        className="w-20 rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted">Channel</label>
                      <select
                        value={stepChannel}
                        onChange={(e) => setStepChannel(e.target.value)}
                        className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                      >
                        <option value="EMAIL">EMAIL</option>
                        <option value="SMS">SMS</option>
                      </select>
                    </div>
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Subject (optional)</label>
                      <input
                        value={stepSubject}
                        onChange={(e) => setStepSubject(e.target.value)}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm"
                      />
                    </div>
                    <button
                      onClick={() => handleAddStep(s.id)}
                      disabled={busy}
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                    >
                      Add
                    </button>
                  </div>
                )}
                {enrollingSequenceId === s.id && (
                  <div className="mt-2 flex items-end gap-2 border-t border-border pt-2">
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Contact</label>
                      <select
                        value={enrollContactId}
                        onChange={(e) => setEnrollContactId(e.target.value)}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      >
                        <option value="">Select a contact...</option>
                        {contacts.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.contact_name || c.company || c.email || c.id}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      onClick={() => handleEnroll(s.id)}
                      disabled={busy || !enrollContactId}
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                    >
                      Enroll
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
