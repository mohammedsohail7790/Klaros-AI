"use client";

import { useState } from "react";
import Link from "next/link";
import { forgotPassword } from "@/lib/api";
import { friendlyAuthError } from "@/lib/authErrors";
import { SLOW_MESSAGE, useSlowHint } from "@/lib/useSlowHint";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import AuthLayout from "@/components/auth/AuthLayout";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const slow = useSlowHint(loading);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(friendlyAuthError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout
      headline="Locked out? It happens. We'll get you back in."
      title="Reset your password"
      subtitle="Enter the email you signed up with and we'll send you a link."
      footer={
        <Link href="/login" className="font-medium text-accent hover:text-accent-hover">
          Back to sign in
        </Link>
      }
    >
      {sent ? (
        <div role="status" className="space-y-3 text-sm text-muted">
          <p className="text-foreground">Check your inbox.</p>
          <p>
            If an account exists for <strong>{email.trim()}</strong>, a reset link is on its way. It works once and expires in 60 minutes. Nothing arrived? Check your
            spam folder, or try again in a few minutes.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Email">
            <Input required type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          {slow && <p role="status" className="text-xs text-muted">{SLOW_MESSAGE}</p>}
          <Button type="submit" disabled={loading || email.trim() === ""} className="w-full">
            {loading ? "Sending..." : "Send reset link"}
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
