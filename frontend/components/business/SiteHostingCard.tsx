"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError, HostedDomain, HostedUpload, SiteHostingOverview, addHostedDomain, apiBaseUrl, getSiteHosting, publishHostedSite, removeHostedDomain, unpublishHostedSite, uploadHostedSite, verifyHostedDomain,
} from "@/lib/api";

/**
 * Your own site on your own domain. Upload a finished site (one HTML file or a zip), publish it, and attach a domain you own. A domain serves nothing until you prove you control it
 * with one DNS record. Enquiries from your own site reach Klaros with the snippet shown here, so they land in the same leads, consent checks and crew as everything else.
 */
function toBase64(buf: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

const kb = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export function SiteHostingCard({ token }: { token: string | null }) {
  const [data, setData] = useState<SiteHostingOverview | null>(null);
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setData(await getSiteHosting(token));
    } catch {
      setData(null);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(action: () => Promise<unknown>, done?: string) {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await action();
      if (done) setNote(done);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  async function onFile(file: File | undefined) {
    if (!file || !token) return;
    await run(async () => uploadHostedSite(token, file.name, toBase64(await file.arrayBuffer())), "Uploaded as a draft. Publish it when you are ready.");
  }

  async function verify(d: HostedDomain) {
    if (!token) return;
    await run(async () => {
      const r = await verifyHostedDomain(token, d.id);
      setNote(r.verified ? `${d.hostname} is verified.` : "We could not see the DNS record yet. It can take a few minutes (sometimes longer) to appear: try again shortly.");
    });
  }

  if (!data) return null;
  const live = data.uploads.find((u: HostedUpload) => u.status === "PUBLISHED");
  const snippet = `fetch("${apiBaseUrl}/api/v1/public/leads/${data.tenant_id}", {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({ name, email, phone, description, source: "WEB" }),\n});`;

  return (
    <section aria-label="Your own site and domain" className="mt-10 rounded-3xl bg-surface-muted px-6 py-6">
      <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">Your own site and domain</h2>
      <p className="mt-1 max-w-2xl text-[15px] leading-snug text-muted">
        Upload a finished site (one HTML file or a zip with an index.html), publish it, and put it on a domain you own. Until a domain is verified it serves nothing.
      </p>

      <div className="mt-5">
        <label className="klaros-btn-secondary inline-flex cursor-pointer !py-2">
          Upload a site
          <input aria-label="Upload a site" type="file" accept=".html,.htm,.zip" className="sr-only" disabled={busy} onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        <span className="ml-3 text-[13px] text-muted">HTML, CSS, JavaScript, images and fonts. Up to 10 MB.</span>
      </div>

      {data.uploads.length > 0 && (
        <ul className="mt-4 divide-y divide-border rounded-2xl bg-surface px-4">
          {data.uploads.map((u) => (
            <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-[14px]">
              <span className="text-foreground">
                Version {u.version} · {u.source_name} · {u.file_count} files, {kb(u.total_bytes)} ·{" "}
                <span className={u.status === "PUBLISHED" ? "font-medium text-success" : "text-muted"}>{u.status === "PUBLISHED" ? "Live" : u.status === "DRAFT" ? "Draft" : "Previous"}</span>
              </span>
              {u.status !== "PUBLISHED" && (
                <button type="button" disabled={busy} onClick={() => run(() => publishHostedSite(token!, u.id), "Published.")} className="text-accent hover:underline disabled:opacity-50">
                  {u.status === "DRAFT" ? "Publish" : "Make live again"}
                </button>
              )}
              {u.status === "PUBLISHED" && (
                <button type="button" disabled={busy} onClick={() => run(() => unpublishHostedSite(token!), "Taken offline.")} className="text-accent hover:underline disabled:opacity-50">Take offline</button>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3 className="mt-8 text-[15px] font-semibold text-foreground">Domain</h3>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          aria-label="Domain name"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="www.yourbusiness.com"
          className="rounded-xl border border-border bg-surface px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
        />
        <button type="button" disabled={busy || !host.trim()} onClick={() => run(async () => { await addHostedDomain(token!, host.trim()); setHost(""); })} className="klaros-btn-secondary !py-2 disabled:opacity-50">
          Add domain
        </button>
      </div>

      {data.domains.map((d) => (
        <div key={d.id} className="mt-4 rounded-2xl bg-surface px-4 py-4 text-[14px] leading-snug text-foreground">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong>{d.hostname}</strong>
            <span className={d.status === "VERIFIED" ? "font-medium text-success" : "text-muted"}>{d.status === "VERIFIED" ? (live ? "Verified · serving your uploaded site" : "Verified · serving your Klaros site") : "Not verified yet"}</span>
          </div>
          {d.status === "PENDING" && (
            <div className="mt-3 text-muted">
              <p>1. At your domain provider, add a <strong>TXT</strong> record:</p>
              <p className="mt-1 break-all font-mono text-[13px] text-foreground">Name: {d.txt_name}<br />Value: {d.txt_value}</p>
              <p className="mt-3">2. Point the domain at Klaros: an <strong>A</strong> record to <span className="font-mono text-foreground">{d.dns_target.a_record}</span> (or, for a www name, a <strong>CNAME</strong> to <span className="font-mono text-foreground">{d.dns_target.cname}</span>).</p>
              <p className="mt-3">3. Then verify. DNS changes can take a few minutes.</p>
            </div>
          )}
          <div className="mt-3 flex gap-4">
            {d.status === "PENDING" && <button type="button" disabled={busy} onClick={() => verify(d)} className="text-accent hover:underline disabled:opacity-50">Verify</button>}
            <button type="button" disabled={busy} onClick={() => run(() => removeHostedDomain(token!, d.id), "Domain removed.")} className="text-muted hover:underline disabled:opacity-50">Remove</button>
          </div>
        </div>
      ))}

      <h3 className="mt-8 text-[15px] font-semibold text-foreground">Send enquiries from your own site to Klaros</h3>
      <p className="mt-1 text-[14px] text-muted">Call this when your form is submitted. The enquiry becomes a lead and goes through your normal acknowledgement, consent and follow-up.</p>
      <pre className="mt-2 overflow-x-auto rounded-xl bg-surface px-4 py-3 text-[12.5px] text-foreground">{snippet}</pre>

      {note && <p role="status" className="mt-3 text-[14px] text-foreground">{note}</p>}
      {error && <p role="alert" className="mt-3 text-[14px] text-danger">{error}</p>}
    </section>
  );
}
