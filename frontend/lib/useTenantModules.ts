"use client";

import { useEffect, useState } from "react";
import { getTenantModules } from "@/lib/api";

const CACHE_KEY = "klaros_modules";

function cached(): string[] {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

/**
 * The modules this organisation has enabled. The navigation is the same for every business; a module only adds its own tabs.
 * FAILS OPEN: until the backend answers (and if it cannot be reached) no module is assumed, so the core navigation is always there and nothing core is ever hidden.
 * The last answer is remembered for the session so the tabs do not flash in on every page.
 */
export function useTenantModules(token: string | null): string[] {
  const [modules, setModules] = useState<string[]>([]);

  useEffect(() => {
    setModules(cached());
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getTenantModules(token)
      .then((r) => {
        if (cancelled) return;
        setModules(r.modules);
        try {
          sessionStorage.setItem(CACHE_KEY, JSON.stringify(r.modules));
        } catch {
          // best-effort only
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token]);

  return modules;
}
