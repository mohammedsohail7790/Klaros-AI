"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import GradientBackdrop from "@/components/GradientBackdrop";

export default function LoginPage() {
  const router = useRouter();
  const [organizationSlug, setOrganizationSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await login(organizationSlug, email, password);
      sessionStorage.setItem("klaros_access_token", tokens.access_token);
      sessionStorage.setItem("klaros_refresh_token", tokens.refresh_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center overflow-hidden px-6">
      <GradientBackdrop />
      <div className="w-full max-w-sm">
        <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground">
          Klaros AI
        </Link>
        <div className="klaros-glass rounded-2xl p-7">
          <h1 className="font-display text-2xl text-foreground">Welcome back</h1>
          <p className="mt-1 text-sm text-muted">Sign in to your Klaros AI workspace.</p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <Field label="Company slug">
              <Input
                required
                value={organizationSlug}
                onChange={(e) => setOrganizationSlug(e.target.value)}
                placeholder="demo-hvac-company"
              />
            </Field>

            <Field label="Email">
              <Input
                required
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </Field>

            <Field label="Password">
              <Input
                required
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>

            {error && <p className="text-sm text-danger">{error}</p>}

            <Button type="submit" disabled={loading} className="w-full">
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </div>
        <p className="mt-6 text-center text-sm text-muted">
          New to Klaros AI?{" "}
          <Link href="/register" className="font-medium text-accent hover:text-accent-hover">
            Create your company
          </Link>
        </p>
      </div>
    </main>
  );
}
