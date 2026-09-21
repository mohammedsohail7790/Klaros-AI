"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  License,
  createLicense,
  detectExpiringLicenses,
  listLicenses,
  renewLicense,
} from "@/lib/api";

const LICENSE_TYPES = [
  "BUSINESS_LICENSE",
  "CONTRACTOR_LICENSE",
  "LIABILITY_INSURANCE",
  "WORKERS_COMP_INSURANCE",
  "BONDING",
  "CERTIFICATION",
  "PERMIT",
  "OTHER",
];

export default function CompliancePage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [licenses, setLicenses] = useState<License[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [type, setType] = useState("LIABILITY_INSURANCE");
  const [name, setName] = useState("");
  const [licenseNumber, setLicenseNumber] = useState("");
  const [issuingAuthority, setIssuingAuthority] = useState("");
  const [holderName, setHolderName] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [renewingId, setRenewingId] = useState<string | null>(null);
  const [renewExpiry, setRenewExpiry] = useState("");
  const [renewBusy, setRenewBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listLicenses(token);
      setLicenses(result.licenses);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load compliance records.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDetectExpiring() {
    if (!token) return;
    setDetecting(true);
    setError(null);
    try {
      const result = await detectExpiringLicenses(token);
      const total = result.newly_expiring_soon.length + result.newly_expired.length;
      toast.success(
        total === 0
          ? "Nothing newly expiring or expired."
          : `${result.newly_expiring_soon.length} newly expiring soon, ${result.newly_expired.length} newly expired.`
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to check for expiring licenses.");
    } finally {
      setDetecting(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !name.trim() || !expiryDate) return;
    setSubmitting(true);
    setError(null);
    try {
      await createLicense(token, {
        type,
        name: name.trim(),
        license_number: licenseNumber || undefined,
        issuing_authority: issuingAuthority || undefined,
        holder_name: holderName || undefined,
        expiry_date: expiryDate,
      });
      setName("");
      setLicenseNumber("");
      setIssuingAuthority("");
      setHolderName("");
      setExpiryDate("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create record.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRenew(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !renewingId || !renewExpiry) return;
    setRenewBusy(true);
    setError(null);
    try {
      await renewLicense(token, renewingId, { expiry_date: renewExpiry });
      toast.success("Renewed.");
      setRenewingId(null);
      setRenewExpiry("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to renew.");
    } finally {
      setRenewBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <header className="mb-1 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Compliance</h1>
          <div className="flex gap-2">
            <button
              disabled={detecting}
              onClick={handleDetectExpiring}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {detecting ? "Checking..." : "Check for expiring"}
            </button>
            <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
              New record
            </button>
          </div>
        </header>
        <p className="mb-6 text-sm text-muted">
          Business licenses, liability/workers-comp insurance, bonds, permits, and certifications — flagged 30 days
          before they lapse.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : licenses.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No licenses or insurance on file yet."
            action={
              <button onClick={() => setShowCreate(true)} className="klaros-btn-primary">
                New record
              </button>
            }
          />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Name</th>
                  <th className="px-4 py-2">Type</th>
                  <th className="px-4 py-2">Holder</th>
                  <th className="px-4 py-2">Expiry</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {licenses.map((lic) => (
                  <tr key={lic.id} className="border-t border-border">
                    <td className="px-4 py-2">{lic.name}</td>
                    <td className="px-4 py-2 text-muted">{lic.type.replaceAll("_", " ")}</td>
                    <td className="px-4 py-2 text-muted">{lic.holder_name ?? "Business"}</td>
                    <td className="px-4 py-2 text-muted">{lic.expiry_date}</td>
                    <td className="px-4 py-2">
                      <Badge status={lic.status}>{lic.status.replaceAll("_", " ")}</Badge>
                    </td>
                    <td className="px-4 py-2">
                      <button
                        onClick={() => {
                          setRenewingId(renewingId === lic.id ? null : lic.id);
                          setRenewExpiry("");
                        }}
                        className="text-xs underline text-muted hover:text-foreground"
                      >
                        Renew
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {renewingId && (
          <form
            onSubmit={handleRenew}
            className="mt-4 flex flex-wrap items-end gap-2 rounded-lg border border-border p-4"
          >
            <div>
              <label className="block text-xs text-muted">New expiry date</label>
              <input
                type="date"
                required
                value={renewExpiry}
                onChange={(e) => setRenewExpiry(e.target.value)}
                className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={renewBusy}
              className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
            >
              {renewBusy ? "Renewing..." : "Renew"}
            </button>
            <button
              type="button"
              onClick={() => setRenewingId(null)}
              className="text-sm text-muted hover:underline"
            >
              Cancel
            </button>
          </form>
        )}
      </div>

      {showCreate && (
        <Modal title="New compliance record" onClose={() => setShowCreate(false)}>
          <form onSubmit={handleCreate} className="space-y-3">
            <select value={type} onChange={(e) => setType(e.target.value)} className="klaros-input">
              {LICENSE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <input
              required
              placeholder="Name (e.g. General Liability Policy)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="klaros-input"
            />
            <input
              placeholder="License / policy number (optional)"
              value={licenseNumber}
              onChange={(e) => setLicenseNumber(e.target.value)}
              className="klaros-input"
            />
            <input
              placeholder="Issuing authority (optional)"
              value={issuingAuthority}
              onChange={(e) => setIssuingAuthority(e.target.value)}
              className="klaros-input"
            />
            <input
              placeholder="Holder (worker name, or blank for the business)"
              value={holderName}
              onChange={(e) => setHolderName(e.target.value)}
              className="klaros-input"
            />
            <div>
              <label className="klaros-label mb-1 block">Expiry date</label>
              <input
                type="date"
                required
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                className="klaros-input"
              />
            </div>
            {error && <p className="text-sm text-danger">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowCreate(false)} className="klaros-btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={submitting} className="klaros-btn-primary">
                {submitting ? "Creating..." : "Create"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </AppShell>
  );
}
