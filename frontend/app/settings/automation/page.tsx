"use client";

import { useCallback, useEffect, useState } from "react";
import { Settings2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  IntegrationStatusRow,
  NotificationPreferenceRow,
  PolicyRow,
  getNotificationPreferences,
  listAutomationPolicies,
  listIntegrationStatus,
  resetAutomationPolicy,
  setAutomationPolicy,
  setNotificationPreference,
} from "@/lib/api";

const POLICY_OPTIONS = ["AUTO", "APPROVAL_REQUIRED", "BLOCKED"] as const;

const POLICY_COLOR: Record<string, string> = {
  AUTO: "border-success/20 text-success",
  APPROVAL_REQUIRED: "border-warning/25 text-warning",
  BLOCKED: "border-danger/25 text-danger",
};

const NOTIFICATION_TYPES = [
  "APPROVAL_REQUIRED",
  "APPROVAL_APPROVED",
  "APPROVAL_REJECTED",
  "ACTION_EXECUTED",
  "ACTION_FAILED",
  "HIGH_PRIORITY_EXCEPTION",
  "PAYMENT_RECEIVED",
  "INVOICE_OVERDUE",
  "JOB_DELAYED",
  "NEGATIVE_FEEDBACK",
  "NEW_LEAD",
  "MORNING_BRIEF_READY",
  "SYSTEM_ERROR",
];

const ALWAYS_IN_APP = new Set(["APPROVAL_REQUIRED", "ACTION_FAILED", "HIGH_PRIORITY_EXCEPTION"]);

function friendlyToolName(name: string): string {
  return name
    .split(".")
    .slice(1)
    .join(" ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function friendlyType(type: string): string {
  return type
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

export default function AutomationSettingsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [policies, setPolicies] = useState<PolicyRow[] | null>(null);
  const [preferences, setPreferences] = useState<NotificationPreferenceRow[] | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationStatusRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ toolName: string; from: string; to: string } | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [policiesResult, prefsResult, integrationsResult] = await Promise.all([
        listAutomationPolicies(token),
        getNotificationPreferences(token),
        listIntegrationStatus(token),
      ]);
      setPolicies(policiesResult.policies);
      setPreferences(prefsResult.preferences);
      setIntegrations(integrationsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load automation settings.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  function requestPolicyChange(toolName: string, from: string, to: string) {
    if (from === to) return;
    setConfirming({ toolName, from, to });
  }

  async function confirmPolicyChange() {
    if (!token || !confirming) return;
    const { toolName, to } = confirming;
    setBusyKey(toolName);
    setError(null);
    try {
      await setAutomationPolicy(token, toolName, to);
      toast.success(`${friendlyToolName(toolName)} is now ${to.replace("_", " ")}.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to change policy.");
    } finally {
      setBusyKey(null);
      setConfirming(null);
    }
  }

  async function handleReset(toolName: string) {
    if (!token) return;
    setBusyKey(toolName);
    try {
      await resetAutomationPolicy(token, toolName);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reset policy.");
    } finally {
      setBusyKey(null);
    }
  }

  async function handlePreferenceToggle(type: string, channel: string, enabled: boolean) {
    if (!token) return;
    const key = `${type}:${channel}`;
    setBusyKey(key);
    setError(null);
    try {
      await setNotificationPreference(token, type, channel, enabled);
      setPreferences(
        (prev) =>
          prev?.map((p) => (p.type === type && p.channel === channel ? { ...p, enabled } : p)) ?? null
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update preference.");
    } finally {
      setBusyKey(null);
    }
  }

  function prefFor(type: string, channel: string): boolean {
    return preferences?.find((p) => p.type === type && p.channel === channel)?.enabled ?? false;
  }

  function integrationConnected(provider: string): boolean {
    return integrations?.find((i) => i.provider === provider)?.status === "CONNECTED";
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-2">Automation Settings</h1>
        <p className="mb-6 text-sm text-muted">
          Decide what Klaros is allowed to do automatically, and how it should notify you when it needs
          your attention.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : (
          <>
            <section className="mb-10">
              <h2 className="mb-3 text-sm font-medium text-muted">Automation Policies</h2>
              {!policies || policies.length === 0 ? (
                <EmptyState icon={Settings2} title="No configurable automation actions found." />
              ) : (
                <div className="klaros-table-wrap">
                  <table className="w-full text-sm">
                    <thead className="bg-surface text-left text-xs text-muted">
                      <tr>
                        <th className="px-3 py-2">Action</th>
                        <th className="px-3 py-2">Current Policy</th>
                        <th className="px-3 py-2">Default</th>
                        <th className="px-3 py-2">Last Changed</th>
                        <th className="px-3 py-2">Set to</th>
                        <th className="px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {policies.map((p) => (
                        <tr key={p.tool_name} className="border-t border-border">
                          <td className="px-3 py-2">
                            <div>{friendlyToolName(p.tool_name)}</div>
                            <div className="text-[10px] text-muted-foreground">{p.tool_name}</div>
                          </td>
                          <td className="px-3 py-2">
                            <span
                              className={`rounded-full border px-2 py-0.5 text-[10px] ${
                                POLICY_COLOR[p.current_policy] ?? "border-border-strong"
                              }`}
                            >
                              {p.system_blocked ? "PLATFORM BLOCKED" : p.current_policy}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-muted">{p.default_policy}</td>
                          <td className="px-3 py-2 text-muted">
                            {p.updated_at ? new Date(p.updated_at).toLocaleString() : "—"}
                          </td>
                          <td className="px-3 py-2">
                            {p.system_blocked ? (
                              <span className="text-xs text-muted-foreground">not configurable</span>
                            ) : (
                              <div className="flex gap-1">
                                {POLICY_OPTIONS.map((opt) => (
                                  <button
                                    key={opt}
                                    disabled={busyKey === p.tool_name || opt === p.current_policy}
                                    onClick={() => requestPolicyChange(p.tool_name, p.current_policy, opt)}
                                    className={`rounded-md border px-2 py-1 text-[10px] disabled:opacity-40 ${
                                      opt === p.current_policy
                                        ? "border-border-strong bg-surface-muted"
                                        : "border-border-strong hover:bg-surface-muted"
                                    }`}
                                  >
                                    {opt.replace("_", " ")}
                                  </button>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {p.has_override && !p.system_blocked && (
                              <button
                                disabled={busyKey === p.tool_name}
                                onClick={() => handleReset(p.tool_name)}
                                className="text-[10px] text-muted underline hover:text-foreground"
                              >
                                reset to default
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section>
              <h2 className="mb-1 text-sm font-medium text-muted">Notifications</h2>
              <p className="mb-3 text-xs text-muted-foreground">
                Email: {integrationConnected("sendgrid") ? "connected" : "NOT CONNECTED — no SENDGRID_API_KEY configured"} · SMS:{" "}
                {integrationConnected("twilio") ? "connected" : "NOT CONNECTED — no TWILIO_ACCOUNT_SID configured"}. In-app
                notifications are always real and always delivered.
              </p>
              <div className="klaros-table-wrap">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-left text-xs text-muted">
                    <tr>
                      <th className="px-3 py-2">Notification</th>
                      <th className="px-3 py-2">In-app</th>
                      <th className="px-3 py-2">Email</th>
                      <th className="px-3 py-2">SMS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {NOTIFICATION_TYPES.map((type) => (
                      <tr key={type} className="border-t border-border">
                        <td className="px-3 py-2">{friendlyType(type)}</td>
                        {(["IN_APP", "EMAIL", "SMS"] as const).map((channel) => {
                          const locked = channel === "IN_APP" && ALWAYS_IN_APP.has(type);
                          const key = `${type}:${channel}`;
                          return (
                            <td key={channel} className="px-3 py-2">
                              <input
                                type="checkbox"
                                checked={locked || prefFor(type, channel)}
                                disabled={locked || busyKey === key}
                                onChange={(e) => handlePreferenceToggle(type, channel, e.target.checked)}
                              />
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        {confirming && (
          <Modal title="Confirm policy change" onClose={() => setConfirming(null)}>
            <p className="text-sm text-foreground">
              Change <span className="font-medium">{friendlyToolName(confirming.toolName)}</span> from{" "}
              <span className="font-medium">{confirming.from.replace("_", " ")}</span> to{" "}
              <span className="font-medium">{confirming.to.replace("_", " ")}</span>?
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setConfirming(null)} className="klaros-btn-secondary">
                Cancel
              </button>
              <button onClick={confirmPolicyChange} className="klaros-btn-primary">
                Confirm
              </button>
            </div>
          </Modal>
        )}
      </div>
    </AppShell>
  );
}
