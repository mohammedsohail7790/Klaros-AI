"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, ClinicCleanupPreview, applyClinicCleanup, getClinicCleanup } from "@/lib/api";

/** Duplicate and junk clinic entries. Shows what would be kept and what would be suspended (reversible, never deleted); nothing changes until the owner confirms. */
export function ClinicCleanup({ token, onDone }: { token: string; onDone: () => void }) {
  const [preview, setPreview] = useState<ClinicCleanupPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setPreview(await getClinicCleanup(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to check the clinic list.");
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function apply() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const r = await applyClinicCleanup(token, preview.suggested_ids);
      setDone(r.suspended);
      await load();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't apply. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  if (!preview) return error ? <p role="alert" className="mb-4 text-danger">{error}</p> : null;
  const count = preview.suggested_ids.length;
  if (count === 0 && done === null) return null;
  return (
    <section aria-label="Clinic list clean-up" className="mb-6 rounded-3xl bg-surface-muted p-6">
      {done !== null && count === 0 ? (
        <p className="text-[15px] text-foreground">{done} duplicate or junk entr{done === 1 ? "y" : "ies"} suspended. They stay on the Providers page under Inactive.</p>
      ) : (
        <>
          <p className="text-[17px] font-semibold tracking-tight text-foreground">{count} clinic entr{count === 1 ? "y looks" : "ies look"} like duplicates or junk</p>
          <p className="mt-1 text-[14px] text-muted">The best entry of each group is kept. The others are suspended, not deleted, so you can turn them back on.</p>
          <ul className="mt-3 space-y-1 text-[14px] text-foreground">
            {preview.groups.map((g) => (
              <li key={g.keep.id}>Keep <strong>{g.keep.name}</strong>, suspend {g.redundant.map((r) => r.name).join(", ")}</li>
            ))}
            {preview.junk.map((j) => (
              <li key={j.id}>Suspend <strong>{j.name}</strong> (looks like a test or list page)</li>
            ))}
          </ul>
          {error && <p role="alert" className="mt-3 text-danger">{error}</p>}
          <button type="button" disabled={busy} onClick={apply} className="klaros-btn-primary mt-4 disabled:opacity-50">Suspend {count} entr{count === 1 ? "y" : "ies"}</button>
        </>
      )}
    </section>
  );
}
