"use client";

import { useEffect, useState } from "react";
import { MAX_QTY } from "@/lib/storeCart";

/**
 * A quantity box you can clear and retype: the text is held as typed, and a valid whole number from 1 to 99 is passed on as soon as it is complete.
 * An empty or invalid box does nothing (it never removes the item); leaving the box puts the real quantity back.
 */
export function QtyInput({ value, onCommit, label, id }: { value: number; onCommit: (n: number) => void; label: string; id?: string }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <input
      id={id}
      type="number"
      inputMode="numeric"
      min={1}
      max={MAX_QTY}
      value={text}
      aria-label={label}
      className="h-10 w-20 rounded-xl bg-surface-muted px-3 text-foreground"
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (Number.isInteger(n) && n >= 1 && n <= MAX_QTY) onCommit(n);
      }}
      onBlur={() => setText(String(value))}
    />
  );
}
