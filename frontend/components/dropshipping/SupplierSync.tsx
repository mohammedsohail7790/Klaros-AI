"use client";

import { useCallback, useEffect, useState } from "react";
import { ProblemAlert } from "@/components/dropshipping/parts";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { SyncSummary, SyncableSupplier, describeError, listSuppliers, runSupplierSync, saveSyncSettings } from "@/lib/dropshippingApi";

const Count = ({ n, label }: { n?: number; label: string }) => (n ? <li>{n} {label}</li> : null);

/** What a run did (or would do), in plain words. */
function Report({ r }: { r: SyncSummary }) {
  if (r.error) return <Alert variant="danger">{r.error}</Alert>;
  return (
    <div className="space-y-2 text-sm">
      <ul className="list-disc pl-5">
        <li>{r.matched ?? 0} items matched the feed ({r.unchanged ?? 0} already up to date).</li>
        <Count n={r.to_available} label="back in stock" />
        <Count n={r.to_out_of_stock} label="now sold out" />
        <Count n={r.prices_changed} label="prices changed" />
        <Count n={r.costs_changed} label="costs updated" />
        <Count n={r.not_in_feed} label="items not in the feed (left as they are)" />
        <Count n={r.unknown_in_feed} label="feed items you do not sell (ignored)" />
      </ul>
      {r.availability_held && <Alert variant="warning">Stock changes were held: too many items would sell out at once. Check the feed, then apply with the box ticked.</Alert>}
      {(r.held_prices?.length ?? 0) > 0 && (
        <Alert variant="warning">
          <div>{r.held_price_count} price changes are bigger than the limit and were held:</div>
          <ul className="mt-1 list-disc pl-5">{r.held_prices!.map((h) => <li key={h.sku_code}>{h.sku_code}: ${h.from} to ${h.to} ({h.change_percent}%)</li>)}</ul>
        </Alert>
      )}
      {(r.problems?.length ?? 0) > 0 && <ul className="list-disc pl-5 text-muted">{r.problems!.map((p) => <li key={p}>{p}</li>)}</ul>}
    </div>
  );
}

function SupplierCard({ s, token, manage, onSaved }: { s: SyncableSupplier; token: string; manage: boolean; onSaved: (s: SyncableSupplier) => void }) {
  const sync = s.sync;
  const [url, setUrl] = useState(sync?.feed_url ?? "");
  const [markup, setMarkup] = useState(sync?.markup_percent ?? "");
  const [buffer, setBuffer] = useState(String(sync?.stock_buffer ?? 0));
  const [maxMove, setMaxMove] = useState(sync?.max_price_change_percent ?? "30");
  const [daily, setDaily] = useState(sync?.sync_enabled ?? false);
  const [csv, setCsv] = useState("");
  const [force, setForce] = useState(false);
  const [preview, setPreview] = useState<SyncSummary | null>(null);
  const [applied, setApplied] = useState<SyncSummary | null>(null);
  const [refusal, setRefusal] = useState<{ message: string; problems: string[] } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setRefusal(null);
    try {
      await fn();
    } catch (err) {
      setRefusal(describeError(err, "That was refused."));
    } finally {
      setBusy(false);
    }
  }
  const save = () => act(async () => {
    setSaved(false);
    onSaved(await saveSyncSettings(token, s.id, { feed_url: url.trim() || null, markup_percent: markup.trim() || null, stock_buffer: Number(buffer) || 0, max_price_change_percent: maxMove.trim() || "30", sync_enabled: daily }));
    setSaved(true);
  });
  const run = (dry: boolean) => act(async () => {
    setApplied(null);
    const body = { dry_run: dry, force, ...(csv.trim() ? { csv_text: csv } : {}) };
    const out = await runSupplierSync(token, s.id, body);
    if (dry) setPreview(out); else { setApplied(out); setPreview(null); }
  });

  const last = sync?.last_sync_summary;
  return (
    <section aria-label={`${s.name} sync`} className="space-y-4 rounded-2xl bg-surface-muted p-5">
      <h3 className="text-lg font-semibold text-foreground">{s.name}</h3>
      <p className="text-sm text-muted">
        {sync?.last_sync_at ? `Last run ${new Date(sync.last_sync_at).toLocaleString()}${last?.error ? `, failed: ${last.error}` : last ? `: ${last.matched ?? 0} items matched, ${last.to_out_of_stock ?? 0} sold out, ${last.prices_changed ?? 0} prices changed.` : "."}` : "Never run."}
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Feed link (https)"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" disabled={!manage} /></Field>
        <Field label="Markup on cost (%), blank = never change prices"><Input inputMode="decimal" value={markup} onChange={(e) => setMarkup(e.target.value)} placeholder="50" disabled={!manage} /></Field>
        <Field label="Sold out at or below this quantity"><Input inputMode="numeric" value={buffer} onChange={(e) => setBuffer(e.target.value)} disabled={!manage} /></Field>
        <Field label="Hold price moves bigger than (%)"><Input inputMode="decimal" value={maxMove} onChange={(e) => setMaxMove(e.target.value)} disabled={!manage} /></Field>
      </div>
      <label className="flex items-start gap-3 text-[15px]"><input type="checkbox" className="mt-1" checked={daily} disabled={!manage} onChange={(e) => setDaily(e.target.checked)} /><span>Sync this feed automatically every day</span></label>
      {refusal && <ProblemAlert message={refusal.message} problems={refusal.problems} />}
      {saved && <Alert variant="success">Saved.</Alert>}
      {manage && (
        <>
          <Button disabled={busy} onClick={save}>Save sync settings</Button>
          <div className="space-y-3 border-t border-black/10 pt-4">
            <p className="text-sm text-muted">Try it first. Leave the box empty to read the feed link, or paste a CSV (columns: the supplier&apos;s item code, cost, quantity).</p>
            <textarea aria-label="Feed CSV" className="h-28 w-full rounded-xl bg-background p-3 font-mono text-xs" value={csv} onChange={(e) => { setCsv(e.target.value); setPreview(null); }} />
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" disabled={busy} onClick={() => run(true)}>Preview (changes nothing)</Button>
              <Button disabled={busy || !preview} onClick={() => run(false)}>Apply this run</Button>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />Also apply held changes</label>
            </div>
            {preview && <div aria-label="Preview result"><Report r={preview} /></div>}
            {applied && <div aria-label="Applied result"><Alert variant="success">Applied.</Alert><Report r={applied} /></div>}
          </div>
        </>
      )}
    </section>
  );
}

/** Each supplier's daily stock and price sync: the feed link, the markup rule, and a preview-then-apply run by hand. */
export default function SupplierSync({ token, manage }: { token: string; manage: boolean }) {
  const [suppliers, setSuppliers] = useState<SyncableSupplier[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setSuppliers((await listSuppliers(token)).suppliers as SyncableSupplier[]);
    } catch (err) {
      setError(describeError(err, "Unable to load the suppliers.").message);
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  return (
    <section aria-label="Supplier sync" className="space-y-4">
      <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Supplier stock and price sync</h2>
      <p className="text-sm text-muted">Keeps stock, cost and (with a markup) prices up to date from the supplier&apos;s CSV feed. It never adds products, and an item missing from the feed is left alone.</p>
      {error ? <Alert variant="danger">{error}</Alert> : suppliers === null ? null : suppliers.length === 0 ? <p className="text-sm text-muted">No suppliers yet. Import your catalog first.</p> : suppliers.map((s) => (
        <SupplierCard key={s.id} s={s} token={token} manage={manage} onSaved={(n) => setSuppliers((cur) => cur && cur.map((x) => (x.id === n.id ? { ...x, ...n } : x)))} />
      ))}
    </section>
  );
}
