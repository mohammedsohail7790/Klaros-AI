"use client";

import { useEffect, useState } from "react";
import { Check, Plus } from "lucide-react";
import { Button } from "@/components/store/ui";
import { useStore } from "@/components/store/StoreProvider";

/** One-tap add from the product grid. The button confirms the add itself (the basket count also bumps), so there is no toast to dismiss. */
export function QuickAdd({ sku, slug, name }: { sku: string; slug: string; name: string }) {
  const { add } = useStore();
  const [added, setAdded] = useState(false);
  useEffect(() => {
    if (!added) return;
    const t = setTimeout(() => setAdded(false), 1600);
    return () => clearTimeout(t);
  }, [added]);
  return (
    <Button size="sm" variant={added ? "secondary" : "primary"} aria-label={added ? `${name} added to basket` : `Add ${name} to basket`} onClick={() => { add({ sku, slug, name }, 1); setAdded(true); }}>
      {added ? <Check className="h-4 w-4" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
      {added ? "Added" : "Add"}
    </Button>
  );
}
