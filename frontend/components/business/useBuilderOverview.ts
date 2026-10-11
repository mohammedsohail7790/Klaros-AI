"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, BuilderOverview, getBuilderOverview } from "@/lib/api";

/** Loads the derived Business Builder overview for the signed-in tenant. */
export function useBuilderOverview(token: string | null) {
  const router = useRouter();
  // Held in a ref so the loader's identity depends only on the token — a router object that
  // changes identity between renders must never trigger a re-fetch loop.
  const routerRef = useRef(router);
  routerRef.current = router;
  const [overview, setOverview] = useState<BuilderOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return null;
    setError(null);
    try {
      const o = await getBuilderOverview(token);
      setOverview(o);
      return o;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        routerRef.current.push("/login");
        return null;
      }
      setError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to view this business."
          : err instanceof ApiError
            ? err.message
            : "We couldn't load your business just now. Check your connection and try again."
      );
      return null;
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { overview, error, reload };
}
