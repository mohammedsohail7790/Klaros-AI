"use client";

import { forwardRef, useEffect, useId, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/cn";
import { MAX_QTY } from "@/lib/storeCart";
import { buttonClass } from "@/components/store/styles";

/* The storefront's own small design system, drawn the way apple.com draws things: flat tiles instead of outlined cards, pills for actions, fields filled rather than
   boxed, and very little chrome. Colours come from the .cleanroom-theme tokens (see globals.css). */

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "brand" | "light" | "ghost"; size?: "md" | "lg" | "sm" };

export const Button = forwardRef<HTMLButtonElement, BtnProps>(({ variant = "primary", size = "md", className, ...p }, ref) => (
  <button ref={ref} className={buttonClass(variant, size, className)} {...p} />
));
Button.displayName = "Button";

export function Chip({ tone = "sage", children, className }: { tone?: "sage" | "green" | "apricot" | "danger"; children: React.ReactNode; className?: string }) {
  const t = { sage: "bg-surface-muted text-foreground", green: "bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]", apricot: "bg-accent text-accent-foreground", danger: "bg-red-100 text-red-900" }[tone];
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-medium", t, className)}>{children}</span>;
}

/** The status dot + word for stock: colour is never the only signal. */
export function StockChip({ inStock }: { inStock: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-[15px] font-medium", inStock ? "text-[rgb(var(--color-brand))]" : "text-red-800")}>
      <span aria-hidden className={cn("h-2 w-2 rounded-full", inStock ? "bg-[rgb(var(--color-brand))]" : "bg-red-700")} />
      {inStock ? "In stock" : "Out of stock"}
    </span>
  );
}

/** A flat tile: sage on the cream page (or cream on a sage band). No outline, no shadow. */
export function Card({ className, children, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[24px] bg-surface-muted", className)} {...p}>{children}</div>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-[24px] bg-surface-muted", className)} />;
}

/** A labelled field filled with sage (no box), with its hint and its own error wired for screen readers. */
export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string; wrapperClass?: string }>(
  ({ label, error, hint, wrapperClass, id, className, ...p }, ref) => {
    const auto = useId();
    const fid = id ?? auto;
    return (
      <div className={wrapperClass}>
        <label htmlFor={fid} className="mb-2 block text-sm font-medium text-foreground">{label}</label>
        <input ref={ref} id={fid} aria-invalid={error ? true : undefined} aria-describedby={error ? `${fid}-err` : hint ? `${fid}-hint` : undefined}
          className={cn("h-[52px] w-full rounded-2xl bg-surface-muted px-4 text-[17px] text-foreground placeholder:text-muted focus-visible:bg-surface focus-visible:ring-2 focus-visible:ring-[rgb(var(--color-brand))]", error && "ring-2 ring-red-700", className)} {...p} />
        {error ? <p id={`${fid}-err`} className="mt-2 text-sm text-red-800">{error}</p> : hint ? <p id={`${fid}-hint`} className="mt-2 text-sm text-muted">{hint}</p> : null}
      </div>
    );
  }
);
Input.displayName = "Input";

/**
 * − [ 3 ] +: the box can be cleared and retyped (the text is held as typed and a valid whole number from 1 to 99 is passed on as soon as it is complete; an empty or invalid
 * box does nothing, and leaving it restores the real quantity). The buttons step by one and stop at the limits.
 */
export function Stepper({ value, onCommit, label, id, size = "md" }: { value: number; onCommit: (n: number) => void; label: string; id?: string; size?: "sm" | "md" }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const h = size === "sm" ? "h-9" : "h-11";
  const step = "flex w-10 items-center justify-center rounded-full text-foreground hover:bg-black/5 disabled:opacity-30 disabled:hover:bg-transparent";
  return (
    <div className={cn("inline-flex items-stretch rounded-full bg-surface-muted px-0.5", h)}>
      <button type="button" className={step} aria-label={`Decrease ${label}`} disabled={value <= 1} onClick={() => onCommit(Math.max(1, value - 1))}><Minus className="h-4 w-4" aria-hidden /></button>
      <input id={id} type="number" inputMode="numeric" min={1} max={MAX_QTY} value={text} aria-label={label}
        className="w-10 bg-transparent text-center text-[17px] font-medium [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
        onChange={(e) => { setText(e.target.value); const n = Number(e.target.value); if (Number.isInteger(n) && n >= 1 && n <= MAX_QTY) onCommit(n); }}
        onBlur={() => setText(String(value))} />
      <button type="button" className={step} aria-label={`Increase ${label}`} disabled={value >= MAX_QTY} onClick={() => onCommit(Math.min(MAX_QTY, value + 1))}><Plus className="h-4 w-4" aria-hidden /></button>
    </div>
  );
}

/** How close the basket is to free shipping, from the owner's real threshold. */
export function FreeShippingMeter({ subtotalCents, thresholdCents }: { subtotalCents: number; thresholdCents: number }) {
  const left = Math.max(0, thresholdCents - subtotalCents);
  const pct = Math.min(100, Math.round((subtotalCents / thresholdCents) * 100));
  const dollars = (c: number) => `$${Math.floor(c / 100)}.${String(c % 100).padStart(2, "0")}`;
  return (
    <div>
      <p className="text-[15px] text-foreground">{left === 0 ? <strong>You have free shipping.</strong> : <>Add <strong>{dollars(left)}</strong> more for free shipping.</>}</p>
      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-background" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Progress to free shipping">
        <div className="h-full rounded-full bg-[rgb(var(--color-brand))] transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
