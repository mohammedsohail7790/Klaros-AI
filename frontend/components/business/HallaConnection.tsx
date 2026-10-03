"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, PlugZap, RefreshCw, Send, Unplug } from "lucide-react";
import {
  ApiError,
  HallaAgent,
  WorkforceSetup,
  checkHallaHealth,
  configureHalla,
  connectHalla,
  disconnectHalla,
  listHallaAgents,
} from "@/lib/api";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { when } from "@/lib/opsLabels";

const fail = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

/**
 * Connect this business to Halla — and see, from the backend's real health check, whether it works.
 *
 * The browser sends the credential once to the Klaros backend, which encrypts it immediately; it is never
 * shown again, never stored in the page, and the form is cleared after every attempt. "Connected" is only
 * ever what the backend reports after a real request to Halla.
 */
export function HallaConnectionPanel({ token, setup, onChanged }: { token: string | null; setup: WorkforceSetup; onChanged: () => void }) {
  const halla = setup.halla;
  const status = setup.status.status;
  const [tenantId, setTenantId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "danger" | "info"; text: string } | null>(null);
  const [agents, setAgents] = useState<HallaAgent[] | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const connected = status === "CONNECTED";

  const loadAgents = useCallback(async () => {
    if (!token) return;
    setAgentsError(null);
    try {
      setAgents((await listHallaAgents(token)).agents);
    } catch (err) {
      setAgents(null);
      setAgentsError(fail(err, "We couldn't load Halla's agents just now."));
    }
  }, [token]);

  useEffect(() => {
    if (connected) loadAgents();
    else setAgents(null);
  }, [connected, loadAgents]);

  if (!halla) return null;

  async function run(label: string, fn: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setNotice(null);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  const connect = (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    const body = { halla_tenant_id: tenantId.trim(), api_key: apiKey.trim(), signing_secret: secret.trim() };
    // The secrets leave the form the moment they are sent, whatever the outcome.
    setApiKey("");
    setSecret("");
    return run("connect", async () => {
      try {
        const r = await connectHalla(token, body);
        setNotice({ kind: r.status === "CONNECTED" ? "success" : "danger", text: r.status === "CONNECTED" ? "Connected — Halla answered a real health check." : r.message });
        setTenantId("");
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't save the connection. Nothing was changed.") });
      }
      onChanged();
    });
  };

  const check = () =>
    run("health", async () => {
      try {
        const r = await checkHallaHealth(token as string);
        setNotice({ kind: r.status === "CONNECTED" ? "success" : "danger", text: r.status === "CONNECTED" ? "Halla answered a real health check." : r.message });
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "The health check could not run.") });
      }
      onChanged();
    });

  const configure = () =>
    run("configure", async () => {
      try {
        const r = await configureHalla(token as string);
        const sent = r.sent;
        setNotice({ kind: "success", text: `Sent to Halla: ${sent.servicesOffered ?? 0} services, ${sent.serviceAreas ?? 0} markets, ${sent.qualificationQuestions ?? 0} qualification questions, ${sent.escalationTriggers ?? 0} escalation rules.` });
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't send the business context to Halla.") });
      }
    });

  const disconnect = () =>
    run("disconnect", async () => {
      try {
        await disconnectHalla(token as string);
        setNotice({ kind: "info", text: "Halla is disconnected. The stored credential was removed." });
      } catch (err) {
        setNotice({ kind: "danger", text: fail(err, "We couldn't disconnect just now.") });
      }
      setConfirmDisconnect(false);
      onChanged();
    });

  const showForm = !halla.has_credential || status === "NOT_CONNECTED" || status === "ERROR" || status === "NEEDS_ATTENTION";

  return (
    <section aria-labelledby="halla-conn" className="mb-8">
      <h2 id="halla-conn" className="klaros-label mb-2">Connect Halla</h2>
      <div className="klaros-card space-y-4 p-5">
        {notice && <Alert variant={notice.kind}>{notice.text}</Alert>}

        {halla.has_credential && (
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="klaros-label">Halla account</dt><dd className="break-all text-foreground">{halla.halla_tenant_id ?? "—"}</dd></div>
            <div><dt className="klaros-label">Last checked</dt><dd className="text-foreground">{halla.last_verified_at ? when(halla.last_verified_at) : "Never"}</dd></div>
            <div><dt className="klaros-label">Signing secret</dt><dd className="text-foreground">{halla.has_signing_secret ? "Stored" : "Missing"}</dd></div>
            {halla.last_error && status !== "CONNECTED" && <div className="sm:col-span-3"><dt className="klaros-label">Last problem</dt><dd className="text-foreground">{halla.last_error}</dd></div>}
          </dl>
        )}

        {halla.webhook_url && (
          <div className="rounded-lg bg-surface-muted/70 p-3 text-sm">
            <p className="klaros-label mb-1">Webhook address to register in Halla</p>
            <div className="flex flex-wrap items-center gap-2">
              <code className="break-all text-xs text-foreground">{halla.webhook_url}</code>
              <button type="button" className="klaros-btn-secondary !px-2 !py-1 text-xs" onClick={() => navigator.clipboard?.writeText(halla.webhook_url as string)}>
                <Copy className="h-3 w-3" aria-hidden="true" /> Copy<span className="sr-only"> webhook address</span>
              </button>
            </div>
            <p className="mt-1 text-xs text-muted">Halla signs every event with the secret you stored here; Klaros ignores anything that doesn&apos;t verify.</p>
          </div>
        )}

        {showForm && (
          <form onSubmit={connect} className="space-y-3" aria-label="Connect Halla">
            <p className="text-sm text-muted">Paste this business&apos;s Halla details. They are encrypted as soon as they are saved and are never shown again.</p>
            <label className="block text-sm">
              <span className="klaros-label">Halla tenant ID</span>
              <input className="klaros-input mt-1" value={tenantId} onChange={(e) => setTenantId(e.target.value)} autoComplete="off" spellCheck={false} />
            </label>
            <label className="block text-sm">
              <span className="klaros-label">Halla API key</span>
              <input className="klaros-input mt-1" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password" spellCheck={false} />
            </label>
            <label className="block text-sm">
              <span className="klaros-label">Webhook signing secret</span>
              <input className="klaros-input mt-1" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" spellCheck={false} />
            </label>
            <Button type="submit" disabled={busy !== null || !tenantId.trim() || !apiKey.trim() || !secret.trim()}>
              <PlugZap className="h-4 w-4" aria-hidden="true" /> {busy === "connect" ? "Connecting…" : halla.has_credential ? "Reconnect Halla" : "Connect Halla"}
            </Button>
          </form>
        )}

        {halla.has_credential && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={check} disabled={busy !== null}><RefreshCw className="h-3.5 w-3.5" aria-hidden="true" /> {busy === "health" ? "Checking…" : "Check connection"}</Button>
            <Button variant="secondary" size="sm" onClick={configure} disabled={busy !== null || !connected} title={connected ? undefined : "Connect Halla first"}><Send className="h-3.5 w-3.5" aria-hidden="true" /> {busy === "configure" ? "Sending…" : "Send business context to Halla"}</Button>
            {confirmDisconnect ? (
              <span className="inline-flex items-center gap-2 text-sm">
                Remove the stored credential?
                <Button variant="danger" size="sm" onClick={disconnect} disabled={busy !== null}>Yes, disconnect</Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(false)}>Cancel</Button>
              </span>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(true)} disabled={busy !== null}><Unplug className="h-3.5 w-3.5" aria-hidden="true" /> Disconnect</Button>
            )}
          </div>
        )}

        {connected && (
          <div>
            <h3 className="klaros-label mb-1.5">Halla&apos;s agents</h3>
            {agentsError ? (
              <p role="alert" className="text-sm text-danger">{agentsError}</p>
            ) : agents === null ? (
              <p className="text-sm text-muted">Loading agents…</p>
            ) : agents.length === 0 ? (
              <p className="text-sm text-muted">Halla reports no agents yet. Agents are managed in Halla.</p>
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border" aria-label="Halla agents">
                {agents.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                    <span className="text-foreground">{a.name}{a.role && <span className="text-muted"> · {a.role}</span>}</span>
                    <span className="text-xs text-muted">{a.available === true ? "Available" : a.available === false ? "Unavailable" : a.status ?? "Status not reported"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
