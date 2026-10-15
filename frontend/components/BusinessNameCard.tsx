"use client";

import { useEffect, useState } from "react";
import { ApiError, getBusinessName, renameBusiness } from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";

/** The business's name, changed in one place: it updates the organisation, the Medical Tourism profile, the Business Blueprint (kept as a new version) and the AI team's topics together. Owner only. */
export function BusinessNameCard({ token, role }: { token: string | null; role: string | undefined }) {
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token || role !== "OWNER") return;
    getBusinessName(token).then((r) => { setName(r.name); setSaved(r.name); }).catch(() => undefined);
  }, [token, role]);

  if (role !== "OWNER" || !saved) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const r = await renameBusiness(token, name);
      setSaved(r.name);
      setName(r.name);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't save. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Business name" className="klaros-card mb-6 p-5 sm:p-6">
      <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Business name</h2>
      <p className="mt-1 text-sm text-muted">Changing it updates your profile, your Business Blueprint (the earlier version is kept) and the topics your AI team writes from. Pages and emails use the new name from now on. A website draft already made keeps the old name until you generate a new one.</p>
      <form onSubmit={save} className="mt-4 max-w-sm space-y-4">
        {done && <Alert variant="success">The business name was changed.</Alert>}
        {error && <Alert variant="danger">{error}</Alert>}
        <Field label="Business name">
          <Input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Button type="submit" disabled={busy || !token || name.trim().length < 2 || name.trim() === saved}>{busy ? "Saving..." : "Save name"}</Button>
      </form>
    </section>
  );
}
