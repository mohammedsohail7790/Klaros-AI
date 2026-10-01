"use client";

import { useCallback, useEffect, useState } from "react";
import { FileText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MedicalTourismProcedure,
  createMedicalTourismProcedure,
  listMedicalTourismProcedures,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";

const STATUS_TABS = ["ALL", "ACTIVE", "INACTIVE"];

export default function MedicalTourismProceduresPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [procedures, setProcedures] = useState<MedicalTourismProcedure[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("ALL");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listMedicalTourismProcedures(token, {
        status: status === "ALL" ? undefined : status,
        category: category || undefined,
        limit: 50,
      });
      setProcedures(result.procedures);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load procedures.");
    } finally {
      setLoading(false);
    }
  }, [token, status, category]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title={`Procedures (${total})`}
          description="The tenant's Medical Tourism treatment/procedure catalog."
          icon={FileText}
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New procedure
            </button>
          }
        />

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"
              }`}
            >
              {s}
            </button>
          ))}
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="Category"
            className="ml-auto w-48 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : procedures.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No procedures yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New procedure
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Category</th>
                  <th className="px-4 py-2">Typical destinations</th>
                  <th className="px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {procedures.map((p) => (
                  <tr key={p.id} className="border-t border-border hover:bg-surface">
                    <td className="px-4 py-2 font-medium text-foreground">{p.name}</td>
                    <td className="px-4 py-2 text-muted">{p.category || "—"}</td>
                    <td className="px-4 py-2 text-muted">{p.typical_destination_countries || "—"}</td>
                    <td className="px-4 py-2">
                      <Badge status={p.status}>{p.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && token && (
        <CreateProcedureModal token={token} onClose={() => setShowCreate(false)} onCreated={load} />
      )}
    </AppShell>
  );
}

function CreateProcedureModal({
  token,
  onClose,
  onCreated,
}: {
  token: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [destinations, setDestinations] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createMedicalTourismProcedure(token, {
        name,
        category: category || undefined,
        description: description || undefined,
        typical_destination_countries: destinations || undefined,
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create procedure.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="New procedure" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input required placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} className="klaros-input" />
        <input placeholder="Category" value={category} onChange={(e) => setCategory(e.target.value)} className="klaros-input" />
        <input
          placeholder="Typical destination countries"
          value={destinations}
          onChange={(e) => setDestinations(e.target.value)}
          className="klaros-input"
        />
        <textarea
          placeholder="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="klaros-input"
          rows={3}
        />
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="klaros-btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="klaros-btn-primary">
            {submitting ? "Creating..." : "Create procedure"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
