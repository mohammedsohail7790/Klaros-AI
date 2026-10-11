"use client";

import { useEffect, useState } from "react";

/**
 * True once a request has been running longer than `afterMs`. Used to say something honest ("the server is waking up") instead of leaving a button that says
 * "Signing in..." for up to a minute when the hosting plan has put the API to sleep.
 */
export function useSlowHint(active: boolean, afterMs = 7000): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return;
    }
    const t = setTimeout(() => setSlow(true), afterMs);
    return () => clearTimeout(t);
  }, [active, afterMs]);
  return slow;
}

export const SLOW_MESSAGE = "Still working. Klaros was idle and is waking up, which can take up to a minute. Please keep this page open.";
