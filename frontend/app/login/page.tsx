"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, getMedicalTourismMode, login } from "@/lib/api";
import { setSession } from "@/lib/session";
import { friendlyAuthError, safeNextPath } from "@/lib/authErrors";
import { SLOW_MESSAGE, useSlowHint } from "@/lib/useSlowHint";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import AuthLayout from "@/components/auth/AuthLayout";

const TRUST_POINTS = [
  "Every AI action is logged with a full audit trail",
  "Your data stays scoped to your own workspace",
  "Patient and customer information is shared only with consent",
];

interface Workspace {
  name: string;
  slug: string;
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [workspaceCode, setWorkspaceCode] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const slow = useSlowHint(loading);
  const notice = params.get("reset") === "1" ? "Your password was changed. Sign in with the new one." : null;

  async function signIn(slug: string | null) {
    setError(null);
    setLoading(true);
    try {
      const tokens = await login(slug, email.trim(), password);
      setSession(tokens);
      // A page the person asked for wins; otherwise a Medical Tourism owner lands on Today and everyone else on the builder.
      let home = "/business";
      try {
        if ((await getMedicalTourismMode(tokens.access_token)).pilot_console === true) home = "/medical-tourism/overview";
      } catch {
        // the mode is a convenience: never block signing in on it
      }
      router.push(safeNextPath(params.get("next"), home));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.code === "multiple_workspaces" && Array.isArray(err.details?.workspaces)) {
        setWorkspaces(err.details.workspaces as Workspace[]);
      } else {
        setWorkspaces(null);
        setError(friendlyAuthError(err));
      }
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    void signIn(workspaceCode.trim() || null);
  }

  return (
    <AuthLayout
      headline="Build and operate your business with AI."
      points={TRUST_POINTS}
      title="Welcome back"
      subtitle="Sign in to your Klaros AI workspace."
      footer={
        <>
          New to Klaros AI?{" "}
          <Link href="/register" className="font-medium text-accent hover:text-accent-hover">
            Create your account
          </Link>
        </>
      }
    >
      {workspaces ? (
        <div role="group" aria-label="Choose a workspace" className="space-y-3">
          <p className="text-sm text-muted">This email belongs to more than one workspace. Which one do you want to open?</p>
          {workspaces.map((w) => (
            <Button key={w.slug} type="button" variant="secondary" className="w-full justify-start" disabled={loading} onClick={() => void signIn(w.slug)}>
              {w.name}
            </Button>
          ))}
          <button type="button" className="text-sm text-muted underline" onClick={() => setWorkspaces(null)}>
            Back
          </button>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          {notice && <p role="status" className="rounded-3xl bg-surface-muted-muted px-3 py-2 text-sm text-foreground">{notice}</p>}
          <Field label="Email">
            <Input required type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>

          <div>
            <Field label="Password">
              <Input
                required
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <div className="mt-1.5 flex items-center justify-between text-xs">
              <label className="flex items-center gap-1.5 text-muted">
                <input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} /> Show password
              </label>
              <Link href="/forgot-password" className="text-accent hover:text-accent-hover">
                Forgot password?
              </Link>
            </div>
          </div>

          <details className="text-sm text-muted">
            <summary className="cursor-pointer">Have several workspaces?</summary>
            <div className="mt-2">
              <Field label="Workspace code (optional)">
                <Input value={workspaceCode} onChange={(e) => setWorkspaceCode(e.target.value)} placeholder="your-company" />
              </Field>
            </div>
          </details>

          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          {slow && <p role="status" className="text-xs text-muted">{SLOW_MESSAGE}</p>}

          <Button type="submit" disabled={loading} className="w-full">
            {loading ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
