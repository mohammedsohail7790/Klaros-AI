"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getBuilderOverview, type BuilderStage } from "@/lib/api";
import { StageTracker } from "./StageTracker";

/**
 * Shows where a standalone screen (website, workforce…) sits in the Business
 * Builder journey, when the tenant has one. Entirely optional: if there is no
 * journey, or the overview can't be loaded, it renders nothing — a screen must
 * never break because the journey context is unavailable.
 */
export function BuilderContextBar({ token, activeKey, note }: { token: string | null; activeKey: string; note?: string }) {
  const [stages, setStages] = useState<BuilderStage[] | null>(null);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const o = await getBuilderOverview(token);
        if (!cancelled && o?.journey) setStages(o.stages);
      } catch {
        /* no journey context — render nothing */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);
  if (!stages) return null;
  return (
    <div className="mb-2">
      <StageTracker stages={stages} activeKey={activeKey} />
      {note && (
        <p className="-mt-3 mb-4 text-sm text-muted">
          {note}{" "}
          <Link href="/business/map" className="underline underline-offset-2">
            Back to your business map
          </Link>
        </p>
      )}
    </div>
  );
}
