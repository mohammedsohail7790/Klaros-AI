"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { register } from "@/lib/api";
import { setSession } from "@/lib/session";
import { friendlyAuthError, passwordHint } from "@/lib/authErrors";
import { SLOW_MESSAGE, useSlowHint } from "@/lib/useSlowHint";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import AuthLayout from "@/components/auth/AuthLayout";

const STEPS = [
  "Create your account — 14 days free, no card",
  "Tell Klaros what you're building",
  "Review your Blueprint and build",
];

export default function RegisterPage() {
  const router = useRouter();
  const [organizationName, setOrganizationName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const slow = useSlowHint(loading);
  const hint = passwordHint(password);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!hint.ok) return;
    setError(null);
    setLoading(true);
    try {
      const result = await register(organizationName.trim(), fullName.trim(), email.trim(), password);
      setSession(result.tokens);
      router.push("/business");
    } catch (err) {
      setError(friendlyAuthError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthLayout
      headline="Set up your workspace in a couple of minutes."
      points={STEPS}
      title="Create your account"
      subtitle="Next, you'll tell Klaros what you're building."
      footer={
        <>
          Already have a workspace?{" "}
          <Link href="/login" className="font-medium text-accent hover:text-accent-hover">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Company name">
          <Input required autoComplete="organization" value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} placeholder="Acme Co" />
        </Field>

        <Field label="Your name">
          <Input required autoComplete="name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </Field>

        <Field label="Email">
          <Input required type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>

        <div>
          <Field label="Password">
            <Input
              required
              minLength={8}
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <div className="mt-1.5 flex items-center justify-between text-xs">
            <span className={hint.ok ? "text-success" : "text-muted"}>{hint.text}</span>
            <label className="flex items-center gap-1.5 text-muted">
              <input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} /> Show
            </label>
          </div>
        </div>

        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {slow && <p role="status" className="text-xs text-muted">{SLOW_MESSAGE}</p>}

        <Button type="submit" disabled={loading || !hint.ok} className="w-full">
          {loading ? "Creating..." : "Create account"}
        </Button>

        <p className="text-xs text-muted-foreground">
          By creating an account you agree to the{" "}
          <Link href="/terms" className="underline">
            Terms of Service
          </Link>{" "}
          and the{" "}
          <Link href="/privacy" className="underline">
            Privacy Policy
          </Link>
          .
        </p>
      </form>
    </AuthLayout>
  );
}
