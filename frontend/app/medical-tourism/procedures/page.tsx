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
import { Hero } from "@/components/ui/layout";
import { ClinicOffers } from "@/components/mt/ClinicOffers";

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

  const [offersRefresh, setOffersRefresh] = useState(0);
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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <Hero
          title={`Procedures (${total})`}
          lede="The treatments you arrange, and which clinics offer them."
          actions={
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New procedure
            </button>
          }
        />

        <div className="mb-6 flex flex-wrap items-center gap-2">
          {STATUS_TABS.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`klaros-filter-tab ${status === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}
            >
              {s}
            </button>
          ))}
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="Category"
            className="klaros-input ml-auto !w-48 !rounded-full"
          />
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
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
          <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Procedures">
            {procedures.map((p) => (
              <li key={p.id} className="flex flex-col gap-2 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-[17px] font-semibold tracking-tight text-foreground">{p.name}</p>
                  <p className="mt-0.5 text-[14px] text-muted">
                    {[p.category, p.typical_destination_countries].filter(Boolean).join(" · ") || "No category or destinations yet"}
                  </p>
                </div>
                <Badge status={p.status}>{p.status}</Badge>
              </li>
            ))}
          </ul>
        )}

        {token && <ClinicOffers token={token} refreshKey={offersRefresh} />}
      </div>

      {showCreate && token && (
        <CreateProcedureModal
          token={token}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            load();
            setOffersRefresh((n) => n + 1);
          }}
        />
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
