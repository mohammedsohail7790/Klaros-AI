"use client";

import { useEffect, useState } from "react";
import { getMedicalTourismMode } from "@/lib/api";

const CACHE_KEY = "klaros_pilot_mode";

function cached(): boolean {
  try {
    return sessionStorage.getItem(CACHE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * True when the signed-in organisation is a consent-gated Medical Tourism pilot, so the shell shows the focused pilot navigation.
 * FAILS OPEN: until the backend has answered (and if it cannot be reached) the full navigation is shown, so a network error can never
 * hide pages from an organisation that needs them. The last answer is remembered for the session to avoid a flash on each page.
 */
export function usePilotMode(token: string | null): boolean {
  const [pilot, setPilot] = useState(false);

  useEffect(() => {
    if (cached()) setPilot(true);
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getMedicalTourismMode(token)
      .then((m) => {
        if (cancelled) return;
        setPilot(m.pilot_console === true);
        try {
          sessionStorage.setItem(CACHE_KEY, m.pilot_console === true ? "1" : "0");
        } catch {
          // best-effort only
        }
      })
      .catch(() => {
        // keep whatever is shown; never hide navigation because a request failed
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return pilot;
}
