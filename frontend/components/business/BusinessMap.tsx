"use client";

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Bot, Boxes, Building2, Landmark, Plug, Users } from "lucide-react";
import type { BusinessMap as BusinessMapData, BusinessMapNode, ReadinessState } from "@/lib/api";
import { cn } from "@/lib/cn";
import { StatusPill } from "./StatusPill";

const KIND_ICON = {
  actor: Users,
  core: Building2,
  capability: Boxes,
  system: Plug,
  workforce: Bot,
  outcome: Landmark,
} as const;

type Line = { key: string; d: string; source: string; target: string; kind: string };

/**
 * The Business Map: a generic lane layout of whatever nodes/edges the
 * backend derived (customers → front door → Klaros → operations → systems).
 * Nothing about the drawing is specific to a business type. On wide screens
 * the edges are drawn as connectors between the measured cards; on narrow
 * screens the same information is shown as an explicit "connects to" list
 * on each card (so the map never depends on a diagram that doesn't fit).
 */
export function BusinessMap({ map }: { map: BusinessMapData }) {
  const [selected, setSelected] = useState<string | null>("core:klaros");
  const [lines, setLines] = useState<Line[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const byId = useMemo(() => Object.fromEntries(map.nodes.map((n) => [n.id, n])), [map.nodes]);
  const lanes = useMemo(() => {
    const out: BusinessMapNode[][] = map.lanes.map(() => []);
    for (const n of map.nodes) (out[n.lane] ??= []).push(n);
    return out;
  }, [map]);
  const outgoing = useMemo(() => {
    const m: Record<string, { to: BusinessMapNode; kind: string }[]> = {};
    for (const e of map.edges) if (byId[e.target]) (m[e.source] ??= []).push({ to: byId[e.target], kind: e.kind });
    return m;
  }, [map.edges, byId]);
  const incoming = useMemo(() => {
    const m: Record<string, { from: BusinessMapNode; kind: string }[]> = {};
    for (const e of map.edges) if (byId[e.source]) (m[e.target] ??= []).push({ from: byId[e.source], kind: e.kind });
    return m;
  }, [map.edges, byId]);

  const measure = useCallback(() => {
    const c = containerRef.current;
    // Same breakpoint as the lane grid (Tailwind `lg`, a viewport width).
    if (!c || typeof window === "undefined" || window.innerWidth < 1024) {
      setLines([]);
      return;
    }
    const base = c.getBoundingClientRect();
    const next: Line[] = [];
    for (const e of map.edges) {
      const a = nodeRefs.current[e.source]?.getBoundingClientRect();
      const b = nodeRefs.current[e.target]?.getBoundingClientRect();
      if (!a || !b) continue;
      const sameLane = byId[e.source]?.lane === byId[e.target]?.lane;
      const goingRight = a.left < b.left;
      let d: string;
      if (sameLane) {
        const x = a.right - base.left;
        const y1 = a.top + a.height / 2 - base.top;
        const y2 = b.top + b.height / 2 - base.top;
        d = `M ${x} ${y1} C ${x + 36} ${y1}, ${x + 36} ${y2}, ${x} ${y2}`;
      } else {
        const x1 = (goingRight ? a.right : a.left) - base.left;
        const x2 = (goingRight ? b.left : b.right) - base.left;
        const y1 = a.top + a.height / 2 - base.top;
        const y2 = b.top + b.height / 2 - base.top;
        const mx = (x1 + x2) / 2;
        d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
      }
      next.push({ key: `${e.source}|${e.target}|${e.kind}`, d, source: e.source, target: e.target, kind: e.kind });
    }
    setLines(next);
  }, [map.edges, byId]);

  useLayoutEffect(() => {
    measure();
  }, [measure, map]);
  useEffect(() => {
    const c = containerRef.current;
    if (!c || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(c);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  const sel = selected ? byId[selected] : null;

  return (
    <div>
      <div ref={containerRef} className="relative">
        <svg className="pointer-events-none absolute inset-0 hidden h-full w-full lg:block" aria-hidden="true">
          <defs>
            <marker id="bm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
              <path d="M0,0 L8,4 L0,8 z" fill="rgb(var(--color-muted-foreground))" />
            </marker>
            <marker id="bm-arrow-hi" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
              <path d="M0,0 L8,4 L0,8 z" fill="rgb(var(--color-accent-hover))" />
            </marker>
          </defs>
          {lines.map((l) => {
            const hi = selected && (l.source === selected || l.target === selected);
            return (
              <path
                key={l.key}
                d={l.d}
                fill="none"
                strokeWidth={hi ? 2 : 1.25}
                stroke={hi ? "rgb(var(--color-accent-hover))" : "rgb(var(--color-border-strong))"}
                opacity={selected && !hi ? 0.45 : 1}
                markerEnd={hi ? "url(#bm-arrow-hi)" : "url(#bm-arrow)"}
              />
            );
          })}
        </svg>

        <div className="relative grid grid-cols-1 gap-6 lg:grid-cols-6 lg:gap-6">
          {lanes.map((nodes, i) => (
            <section key={map.lanes[i]} aria-label={map.lanes[i]} className="min-w-0">
              <h2 className="klaros-label mb-2 text-center lg:min-h-[2.25rem]">{map.lanes[i]}</h2>
              <ul className="flex flex-col justify-center gap-3 lg:min-h-full">
                {nodes.length === 0 && <li className="hidden text-center text-xs text-muted-foreground lg:block">—</li>}
                {nodes.map((n) => {
                  const Icon = KIND_ICON[n.kind] ?? Boxes;
                  const isSel = selected === n.id;
                  const outs = outgoing[n.id] ?? [];
                  return (
                    <li key={n.id}>
                      <button
                        type="button"
                        ref={(el) => {
                          nodeRefs.current[n.id] = el;
                        }}
                        onClick={() => setSelected(isSel ? null : n.id)}
                        aria-pressed={isSel}
                        className={cn(
                          "klaros-card w-full p-2.5 text-left transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                          n.kind === "core" && "border-accent bg-accent-soft/60",
                          n.planned && "border-dashed",
                          isSel && "ring-2 ring-accent/50"
                        )}
                      >
                        <span className="flex items-start gap-2">
                          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-accent" strokeWidth={2} aria-hidden="true" />
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium leading-snug text-foreground">{n.label}</span>
                            {n.sublabel && <span className="mt-0.5 block text-xs text-muted">{n.sublabel}</span>}
                            {n.state && n.kind !== "core" && n.kind !== "outcome" && n.kind !== "actor" && (
                              <StatusPill state={n.state} className="mt-1.5" />
                            )}
                            {n.kind === "actor" && n.state && n.planned && <StatusPill state={n.state} className="mt-1.5" />}
                          </span>
                        </span>
                        {/* Narrow screens: the connections as text, since the diagram is hidden. */}
                        {outs.length > 0 && (
                          <span className="mt-2 block border-t border-border pt-2 text-xs text-muted lg:hidden">
                            {outs.map((o) => (
                              <span key={o.to.id + o.kind} className="flex items-center gap-1">
                                <ArrowRight className="h-3 w-3 shrink-0" aria-hidden="true" />
                                <span className="text-muted-foreground">{o.kind}</span> {o.to.label}
                              </span>
                            ))}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>

      <div className="klaros-card mt-6 p-5" aria-live="polite">
        {sel ? (
          <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-start">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-foreground">{sel.label}</h3>
                {sel.state && <StatusPill state={sel.state} />}
              </div>
              {sel.why && <p className="mt-1 text-sm text-muted">{sel.why}</p>}
              <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <p className="klaros-label">Receives from</p>
                  {(incoming[sel.id] ?? []).length === 0 ? (
                    <p className="text-muted-foreground">Nothing — this is a starting point.</p>
                  ) : (
                    <ul className="space-y-0.5">
                      {incoming[sel.id].map((i) => (
                        <li key={i.from.id + i.kind}>
                          <button type="button" className="text-left hover:underline" onClick={() => setSelected(i.from.id)}>
                            {i.from.label}
                          </button>{" "}
                          <span className="text-muted-foreground">· {i.kind}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="klaros-label">Sends to</p>
                  {(outgoing[sel.id] ?? []).length === 0 ? (
                    <p className="text-muted-foreground">Nothing — this is an end point.</p>
                  ) : (
                    <ul className="space-y-0.5">
                      {outgoing[sel.id].map((o) => (
                        <li key={o.to.id + o.kind}>
                          <span className="text-muted-foreground">{o.kind} · </span>
                          <button type="button" className="text-left hover:underline" onClick={() => setSelected(o.to.id)}>
                            {o.to.label}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>
            {sel.route && (
              <Link href={sel.route} className="klaros-btn-secondary w-full text-center sm:w-auto">
                Open<span className="sr-only"> {sel.label}</span>
              </Link>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted">Select any part of the map to see why it is there and what it connects to.</p>
        )}
      </div>

      <div className="mt-4" role="group" aria-label="Legend">
        <p className="klaros-label mb-2">What the statuses mean</p>
        <ul className="grid gap-x-6 gap-y-2 text-xs text-muted sm:grid-cols-2">
          {(
            [
              ["CONNECTED", "A live connection exists."],
              ["READY", "A Klaros module is built in and usable."],
              ["AVAILABLE", "A real integration exists; you haven't connected it."],
              ["CONFIGURATION_REQUIRED", "It exists but needs setting up or publishing."],
              ["INTEGRATION_REQUIRED", "No adapter exists yet, so there is nothing to connect."],
              ["PLANNED", "Part of your architecture, but not something Klaros can do today (dashed outline)."],
            ] as [ReadinessState, string][]
          ).map(([st, text]) => (
            <li key={st} className="flex items-start gap-2">
              <StatusPill state={st} className="shrink-0" />
              <span>{text}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">Cards marked “Required” come from your Blueprint; “Suggested” ones come from an industry module.</p>
      </div>
    </div>
  );
}
