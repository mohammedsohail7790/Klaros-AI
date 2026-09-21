"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import GradientBackdrop from "@/components/GradientBackdrop";

const TRUST_POINTS = [
  "Every AI action is logged with a full audit trail",
  "Your data stays scoped to your own workspace",
  "Built for solo operators running a real company",
];

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
    <main className="relative flex min-h-screen overflow-hidden">
      <GradientBackdrop />

      {/* Brand panel — only on larger screens, so mobile stays a single
          focused form like before. Gives the auth flow real identity
          instead of a form floating alone on a blob gradient. */}
      <div className="relative hidden w-[42%] flex-col justify-between overflow-hidden border-r border-border/60 px-12 py-12 lg:flex">
        <div
          className="absolute inset-0 -z-10"
          style={{
            background:
              "linear-gradient(160deg, rgb(var(--color-accent-2) / 0.92) 0%, rgb(var(--color-accent-2)) 55%, rgb(var(--color-accent-2) / 0.85) 100%)",
          }}
        />
        <div
          className="absolute -bottom-24 -left-24 h-96 w-96 rounded-full opacity-30 blur-3xl"
          style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
        />

        <Link href="/" className="font-display block text-xl italic text-white">
          Klaros AI
        </Link>

        <div className="max-w-sm">
          <p className="font-display text-3xl leading-snug text-white">
            The AI operating system for the one-person company.
          </p>
          <ul className="mt-8 space-y-4">
            {TRUST_POINTS.map((point) => (
              <li key={point} className="flex items-start gap-3 text-sm text-white/80">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[rgb(var(--color-accent))]" />
                {point}
              </li>
            ))}
          </ul>
        </div>

        <p className="text-xs text-white/50">© {new Date().getFullYear()} Klaros AI</p>
      </div>

      <div className="flex w-full flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground lg:hidden">
            Klaros AI
          </Link>
          <div className="klaros-glass rounded-2xl p-7 shadow-xl shadow-black/5">
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
      </div>
    </main>
  );
}
