"use client";

import { useEffect } from "react";
import Link from "next/link";

/** Shown when a page crashes, so a person sees a calm message and a way forward instead of a blank screen. */
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("klaros_page_error", error.digest ?? error.message);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div role="alert" className="max-w-md text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Something went wrong on our side</h1>
        <p className="mt-2 text-sm text-muted">
          The page hit an unexpected problem. Your data is safe. Try again, and if it keeps happening please let us know.
        </p>
        {error.digest && <p className="mt-2 text-xs text-muted-foreground">Reference: {error.digest}</p>}
        <div className="mt-6 flex justify-center gap-3 text-sm">
          <button type="button" onClick={reset} className="rounded-xl bg-accent px-4 py-2 font-medium text-accent-foreground hover:bg-accent-hover">
            Try again
          </button>
          <Link href="/contact" className="klaros-card px-4 py-2 hover:bg-surface-muted">
            Contact us
          </Link>
        </div>
      </div>
    </main>
  );
}
