"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, acceptInvite, getInvitePreview } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import GradientBackdrop from "@/components/GradientBackdrop";

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <AcceptInviteInner />
    </Suspense>
  );
}

function AcceptInviteInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [preview, setPreview] = useState<{ organization_name: string; email: string; role: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(true);

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPreview = useCallback(async () => {
    if (!token) {
      setPreviewError("Missing invite token.");
      setLoadingPreview(false);
      return;
    }
    try {
      setPreview(await getInvitePreview(token));
    } catch (err) {
      setPreviewError(
        err instanceof ApiError ? err.message : "This invite link is invalid or has expired."
      );
    } finally {
      setLoadingPreview(false);
    }
  }, [token]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await acceptInvite(token, fullName, password);
      sessionStorage.setItem("klaros_access_token", result.tokens.access_token);
      sessionStorage.setItem("klaros_refresh_token", result.tokens.refresh_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center overflow-hidden px-6 py-12">
      <GradientBackdrop />
      <div className="w-full max-w-sm">
        <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground">
          Klaros AI
        </Link>
        <div className="klaros-glass rounded-2xl p-7">
          {loadingPreview ? (
            <Skeleton />
          ) : previewError || !preview ? (
            <>
              <h1 className="font-display text-2xl text-foreground">Invite not available</h1>
              <p className="mt-2 text-sm text-danger">{previewError}</p>
              <p className="mt-4 text-sm text-muted">
                Ask whoever invited you to send a new invite from Settings → Team.
              </p>
            </>
          ) : (
            <>
              <h1 className="font-display text-2xl text-foreground">Join {preview.organization_name}</h1>
              <p className="mt-1 text-sm text-muted">
                You&apos;ve been invited as <span className="font-medium text-foreground">{preview.role}</span>.
                Set a password to finish joining {preview.email}.
              </p>

              <form onSubmit={handleSubmit} className="mt-6 space-y-4">
                <Field label="Your name">
                  <Input required value={fullName} onChange={(e) => setFullName(e.target.value)} />
                </Field>
                <Field label="Password">
                  <Input
                    required
                    minLength={8}
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </Field>

                {error && <p className="text-sm text-danger">{error}</p>}

                <Button type="submit" disabled={submitting} className="w-full">
                  {submitting ? "Joining..." : "Join workspace"}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
