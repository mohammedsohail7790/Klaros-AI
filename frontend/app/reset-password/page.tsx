"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { resetPassword } from "@/lib/api";
import { friendlyAuthError, passwordHint } from "@/lib/authErrors";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import AuthLayout from "@/components/auth/AuthLayout";

function ResetForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const hint = passwordHint(password);
  const mismatch = confirm.length > 0 && confirm !== password;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!hint.ok || mismatch || confirm !== password) return;
    setError(null);
    setLoading(true);
    try {
      await resetPassword(token, password);
      router.push("/login?reset=1");
    } catch (err) {
      setError(friendlyAuthError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout
      headline="Choose a new password and you're back in."
      title="Choose a new password"
      subtitle="This also signs you out everywhere else."
      footer={
        <Link href="/login" className="font-medium text-accent hover:text-accent-hover">
          Back to sign in
        </Link>
      }
    >
      {!token ? (
        <div role="alert" className="space-y-3 text-sm text-muted">
          <p className="text-foreground">This reset link is incomplete.</p>
          <p>
            Open the link from your email again, or{" "}
            <Link href="/forgot-password" className="text-accent underline">
              ask for a new one
            </Link>
            .
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <Field label="New password">
              <Input required type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <p className={`mt-1.5 text-xs ${hint.ok ? "text-success" : "text-muted"}`}>{hint.text}</p>
          </div>
          <div>
            <Field label="Repeat the new password">
              <Input required type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
            {mismatch && <p className="mt-1.5 text-xs text-danger">The two passwords don't match.</p>}
          </div>
          {error && (
            <p role="alert" className="text-sm text-danger">
              {error}{" "}
              <Link href="/forgot-password" className="underline">
                Ask for a new link
              </Link>
            </p>
          )}
          <Button type="submit" disabled={loading || !hint.ok || mismatch || confirm === ""} className="w-full">
            {loading ? "Saving..." : "Set new password"}
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetForm />
    </Suspense>
  );
}
