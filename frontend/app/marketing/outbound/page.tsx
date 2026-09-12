"use client";

import { useCallback, useEffect, useState } from "react";
import { Contact, List } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  OutboundContactRow,
  OutboundListRow,
  addOutboundContact,
  createOutboundList,
  listOutboundContacts,
  listOutboundLists,
} from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";

export default function OutboundPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [lists, setLists] = useState<OutboundListRow[]>([]);
  const [contacts, setContacts] = useState<OutboundContactRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listName, setListName] = useState("");
  const [selectedList, setSelectedList] = useState<string>("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactCompany, setContactCompany] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [listsResult, contactsResult] = await Promise.all([listOutboundLists(token), listOutboundContacts(token)]);
      setLists(listsResult.lists);
      setContacts(contactsResult.contacts);
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

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-6">Outbound &amp; List Building</h1>

        {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50/30 p-3 text-sm text-red-700">{error}</div>}

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
              <p className="text-sm text-muted">Loading...</p>
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
      </div>
    </AppShell>
  );
}
