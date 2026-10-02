"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, BusinessOperations, getBusinessOperations } from "@/lib/api";

/** Loads the operating read-model. Never signs the user out: an expired session is handled by
 * `useAuth`/the API's silent refresh, and any other failure is shown with a retry. */
export function useOperations(token: string | null) {
  const [ops, setOps] = useState<BusinessOperations | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    if (!token) return null;
    setError(null);
    try {
      const o = await getBusinessOperations(token);
      setOps(o);
      return o;
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to view business operations."
          : err instanceof ApiError
            ? err.message
            : "We couldn't load your operations just now. Check your connection and try again."
      );
      return null;
    }
  }, [token]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { ops, error, reload };
}
