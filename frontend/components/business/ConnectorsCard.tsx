"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/Badge";
import { statusLabel } from "@/lib/integrationLabels";
import {
  ApiError,
  ConnectorRow,
  completeConnector,
  connectIntegration,
  disconnectIntegration,
  getWhatsAppWebhookInfo,
  listConnectors,
  startConnector,
  verifyIntegrationConnection,
} from "@/lib/api";

declare global {
  interface Window {
    Plaid?: { create: (config: Record<string, unknown>) => { open: () => void } };
  }
}

const PLAID_SCRIPT = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

function loadPlaid(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PLAID_SCRIPT;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("The bank connection tool could not load. Check your connection and try again."));
    document.head.appendChild(script);
  });
}

/**
 * Every outside service a business can connect, one card each: QuickBooks, ad accounts, WhatsApp, bank feed, e-signature. The same list for every
 * business. A connector shows what is still missing on the Klaros side (key names only, never values) and the steps in order, so a button is never dead.
 */
export function ConnectorsCard({ token, exclude = [] }: { token: string | null; exclude?: string[] }) {
  const searchParams = useSearchParams();
  const [rows, setRows] = useState<ConnectorRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [hook, setHook] = useState<{ callback_url: string; verify_token: string; signature_check: boolean } | null>(null);
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setRows(await listConnectors(token));
    } catch (err) {
      setRows(null);
      setErrors((e) => ({ ...e, _list: err instanceof ApiError ? err.message : "Unable to load connectors." }));
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const whatsappConnected = rows?.some((r) => r.key === "whatsapp" && (r.status === "CONNECTED" || r.status === "UNVERIFIED"));
  useEffect(() => {
    if (!token || !whatsappConnected) return;
    getWhatsAppWebhookInfo(token).then(setHook).catch(() => setHook(null));
  }, [token, whatsappConnected]);

  const landed = searchParams.get("connector");
  const landedResult = searchParams.get("result");
  const landedDetail = searchParams.get("detail");

  function fail(key: string, err: unknown, fallback: string) {
    setErrors((e) => ({ ...e, [key]: err instanceof ApiError ? err.message : fallback }));
  }

  async function run(key: string, action: () => Promise<void>, fallback: string) {
    if (!token || busy) return;
    setBusy(key);
    setErrors((e) => ({ ...e, [key]: "" }));
    try {
      await action();
    } catch (err) {
      fail(key, err, fallback);
    } finally {
      setBusy(null);
    }
  }

  async function connect(c: ConnectorRow) {
    await run(
      c.key,
      async () => {
        const started = await startConnector(token as string, c.key, c.start_path);
        if (c.mode === "oauth") {
          window.location.href = started.authorization_url;
          return;
        }
        if (c.mode === "link" && started.link_token) {
          await loadPlaid();
          const handler = window.Plaid!.create({
            token: started.link_token,
            onSuccess: async (publicToken: string, metadata: unknown) => {
              try {
                await completeConnector(token as string, c.key, { public_token: publicToken, metadata });
                await load();
              } catch (err) {
                fail(c.key, err, "We couldn't finish connecting that account.");
              }
            },
          });
          handler.open();
        }
      },
      `Unable to start the ${c.name} connection.`,
    );
  }

  async function save(c: ConnectorRow) {
    await run(
      c.key,
      async () => {
        await connectIntegration(token as string, c.key, values[c.key] ?? {});
        setValues((v) => ({ ...v, [c.key]: {} }));
        await load();
      },
      `We couldn't connect ${c.name}. Nothing was saved.`,
    );
  }

  if (!rows) return errors._list ? <p className="text-sm text-danger">{errors._list}</p> : null;

  return (
    <section className="mb-8">
      <h2 className="mb-1 text-lg font-semibold">Connect your accounts</h2>
      <p className="mb-4 text-sm text-muted-foreground">Accounting, ads, WhatsApp, your bank and signatures, each connected in a few clicks.</p>
      {landed && (
        <div
          className={`mb-3 rounded border px-3 py-2 text-sm ${landedResult === "connected" ? "border-success/25 bg-success/[0.06] text-success" : "border-danger/25 bg-danger/[0.06] text-danger"}`}
        >
          {landedResult === "connected" ? "Connected." : `That connection did not complete${landedDetail ? `: ${landedDetail}` : "."}`}
        </div>
      )}
      <div className="grid gap-3">
        {rows.filter((c) => !exclude.includes(c.key)).map((c) => {
          const connected = c.status === "CONNECTED" || c.status === "UNVERIFIED";
          const showSteps = open === c.key;
          return (
            <div key={c.key} className="rounded border border-border bg-surface p-4" data-testid={`connector-${c.key}`}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">{c.name}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">{c.description}</div>
                </div>
                <Badge status={c.status}>{c.built_in ? "Built in" : statusLabel(c.status)}</Badge>
              </div>

              {errors[c.key] && <div className="mt-2 rounded border border-danger/25 bg-danger/[0.06] px-3 py-2 text-xs text-danger">{errors[c.key]}</div>}
              {c.last_error && !connected && !c.built_in && <div className="mt-2 text-xs text-danger">{c.last_error}</div>}

              {!c.built_in && !c.platform_ready && c.mode !== "credentials" && (
                <div className="mt-2 rounded border border-border-strong bg-surface px-3 py-2 text-xs text-muted-foreground">
                  Not switched on for this Klaros yet. Still needed on the server: <span className="font-mono">{c.missing_platform_settings.join(", ")}</span>.
                </div>
              )}

              {!c.built_in && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {c.mode === "credentials" && !connected && (
                    <>
                      {c.fields.map((f) => (
                        <input
                          key={f.name}
                          type={f.secret ? "password" : "text"}
                          autoComplete="off"
                          placeholder={f.label}
                          title={f.help}
                          value={values[c.key]?.[f.name] ?? ""}
                          onChange={(e) => setValues((v) => ({ ...v, [c.key]: { ...(v[c.key] ?? {}), [f.name]: e.target.value } }))}
                          className="min-w-[10rem] flex-1 rounded border border-border-strong bg-surface px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground"
                        />
                      ))}
                      <button onClick={() => save(c)} disabled={busy === c.key} className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50">
                        {busy === c.key ? "Working..." : "Connect"}
                      </button>
                    </>
                  )}
                  {c.mode !== "credentials" && !connected && (
                    <button
                      onClick={() => connect(c)}
                      disabled={busy === c.key || !c.platform_ready}
                      className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
                    >
                      {busy === c.key ? "Working..." : "Connect"}
                    </button>
                  )}
                  {connected && (
                    <>
                      <button
                        onClick={() => run(c.key, async () => { await verifyIntegrationConnection(token as string, c.key); await load(); }, "Check failed.")}
                        disabled={busy === c.key}
                        className="klaros-btn-secondary !px-3 !py-1.5 disabled:opacity-50"
                      >
                        Check connection
                      </button>
                      <button
                        onClick={() => run(c.key, async () => { await disconnectIntegration(token as string, c.key); await load(); }, "Unable to disconnect.")}
                        disabled={busy === c.key}
                        className="rounded border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                      >
                        Disconnect
                      </button>
                    </>
                  )}
                  <button onClick={() => setOpen(showSteps ? null : c.key)} className="text-xs text-muted-foreground underline">
                    {showSteps ? "Hide steps" : "How to set this up"}
                  </button>
                </div>
              )}

              {c.key === "whatsapp" && connected && hook && (
                <div className="mt-3 rounded border border-border-strong px-3 py-2 text-xs text-muted-foreground">
                  <p>In Meta, WhatsApp, Configuration, Webhook, paste these two values, then subscribe to <span className="font-mono">messages</span>:</p>
                  <p className="mt-1">Callback URL: <span className="break-all font-mono">{hook.callback_url}</span></p>
                  <p>Verify token: <span className="font-mono">{hook.verify_token}</span></p>
                  {!hook.signature_check && <p className="mt-1">Add your App secret in the connection to have Meta's signatures checked too.</p>}
                </div>
              )}

              {(showSteps || c.built_in) && (
                <div className="mt-3 text-xs text-muted-foreground">
                  {c.redirect_uri && (
                    <p className="mb-1">
                      Redirect address to register: <span className="font-mono">{c.redirect_uri}</span>
                    </p>
                  )}
                  <ol className="list-decimal space-y-1 pl-5">
                    {c.owner_steps.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
