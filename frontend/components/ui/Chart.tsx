"use client";

import { useId, useState } from "react";

/**
 * A small inline SVG line/area chart for real, already-fetched numeric
 * series (e.g. a 13-week cash forecast) — deliberately built on plain
 * SVG rather than a charting library, since the only series this app
 * ever has are short (a dozen-ish points) and this keeps bundle size
 * and dependency surface at zero. There is no synthetic/interpolated
 * data path here: every point plotted is a value the caller already
 * has, matching the rest of the app's "never fabricate what isn't
 * real" data discipline — a metric with no real history simply doesn't
 * get a chart rather than a fake one.
 */
export function LineChart({
  points,
  height = 120,
  formatValue = (v: number) => v.toLocaleString(),
}: {
  points: { label: string; value: number }[];
  height?: number;
  formatValue?: (v: number) => string;
}) {
  const gradientId = useId();
  const [hover, setHover] = useState<number | null>(null);

  if (points.length < 2) return null;

  const width = 600;
  const padTop = 16;
  const padBottom = 24;
  const padX = 4;
  const plotHeight = height - padTop - padBottom;

  const values = points.map((p) => p.value);
  const min = Math.min(0, ...values);
  const max = Math.max(...values, 1);
  const range = max - min || 1;

  const x = (i: number) => padX + (i / (points.length - 1)) * (width - padX * 2);
  const y = (v: number) => padTop + plotHeight - ((v - min) / range) * plotHeight;

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.value)}`).join(" ");
  const areaPath = `${linePath} L ${x(points.length - 1)} ${padTop + plotHeight} L ${x(0)} ${padTop + plotHeight} Z`;
  const zeroY = y(0);

  return (
    <div className="relative w-full select-none">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" preserveAspectRatio="none" style={{ height }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--color-accent))" stopOpacity="0.28" />
            <stop offset="100%" stopColor="rgb(var(--color-accent))" stopOpacity="0" />
          </linearGradient>
        </defs>
        {min < 0 && (
          <line x1={padX} y1={zeroY} x2={width - padX} y2={zeroY} stroke="rgb(var(--color-border))" strokeDasharray="3 3" />
        )}
        <path d={areaPath} fill={`url(#${gradientId})`} />
        <path d={linePath} fill="none" stroke="rgb(var(--color-accent))" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <g key={i}>
            <rect
              x={x(i) - (width / points.length) / 2}
              y={0}
              width={width / points.length}
              height={height}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover((h) => (h === i ? null : h))}
            />
            <circle
              cx={x(i)}
              cy={y(p.value)}
              r={hover === i ? 4.5 : 3}
              fill="rgb(var(--color-surface))"
              stroke="rgb(var(--color-accent))"
              strokeWidth="2"
              className="transition-all duration-100"
            />
          </g>
        ))}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs shadow-raised"
          style={{ left: `${(x(hover) / width) * 100}%`, top: `${(y(points[hover].value) / height) * 100}%` }}
        >
          <div className="font-semibold text-foreground">{formatValue(points[hover].value)}</div>
          <div className="text-muted-foreground">{points[hover].label}</div>
        </div>
      )}
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{points[0].label}</span>
        <span>{points[points.length - 1].label}</span>
      </div>
    </div>
  );
}

/** A small horizontal bar breakdown for a short list of real named
 * values (e.g. AR aging buckets) — bars scale to the largest value in
 * the set so relative weight reads at a glance. */
export function BarBreakdown({
  bars,
  formatValue = (v: number) => v.toLocaleString(),
}: {
  bars: { label: string; value: number; tone?: "neutral" | "accent" | "success" | "warning" | "danger" }[];
  formatValue?: (v: number) => string;
}) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  const toneClass: Record<string, string> = {
    neutral: "bg-muted-foreground/40",
    accent: "bg-accent",
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-danger",
  };

  return (
    <div className="space-y-2.5">
      {bars.map((b) => (
        <div key={b.label} className="flex items-center gap-3 text-sm">
          <span className="w-24 shrink-0 text-xs text-muted">{b.label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
            <div
              className={`h-full rounded-full ${toneClass[b.tone ?? "neutral"]}`}
              style={{ width: `${(b.value / max) * 100}%` }}
            />
          </div>
          <span className="w-20 shrink-0 text-right text-xs font-medium text-foreground">{formatValue(b.value)}</span>
        </div>
      ))}
    </div>
  );
}
