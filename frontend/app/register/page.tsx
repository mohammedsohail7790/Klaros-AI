"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, register } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import GradientBackdrop from "@/components/GradientBackdrop";

const STEPS = [
  { label: "Create your company", done: true },
  { label: "Set up your first job", done: false },
  { label: "Invite your team (optional)", done: false },
];

export default function RegisterPage() {
  const router = useRouter();
  const [organizationName, setOrganizationName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await register(organizationName, fullName, email, password);
      sessionStorage.setItem("klaros_access_token", result.tokens.access_token);
      sessionStorage.setItem("klaros_refresh_token", result.tokens.refresh_token);
      router.push("/onboarding");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen overflow-hidden">
      <GradientBackdrop />

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
            Set up your workspace in a couple of minutes.
          </p>
          <ol className="mt-8 space-y-4">
            {STEPS.map((step, i) => (
              <li key={step.label} className="flex items-center gap-3 text-sm">
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                    step.done
                      ? "bg-[rgb(var(--color-accent))] text-[rgb(var(--color-accent-2))]"
                      : "border border-white/30 text-white/60"
                  }`}
                >
                  {i + 1}
                </span>
                <span className={step.done ? "text-white" : "text-white/60"}>{step.label}</span>
              </li>
            ))}
          </ol>
        </div>

        <p className="text-xs text-white/50">© {new Date().getFullYear()} Klaros AI</p>
      </div>

      <div className="flex w-full flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground lg:hidden">
            Klaros AI
          </Link>
          <div className="klaros-glass rounded-2xl p-7 shadow-xl shadow-black/5">
            <h1 className="font-display text-2xl text-foreground">Create your company</h1>
            <p className="mt-1 text-sm text-muted">Set up your Klaros AI workspace in a couple of minutes.</p>

            <form onSubmit={handleSubmit} className="mt-6 space-y-4">
              <Field label="Company name">
                <Input
                  required
                  value={organizationName}
                  onChange={(e) => setOrganizationName(e.target.value)}
                  placeholder="Demo HVAC Company"
                />
              </Field>

              <Field label="Your name">
                <Input required value={fullName} onChange={(e) => setFullName(e.target.value)} />
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
                  minLength={8}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>

              {error && <p className="text-sm text-danger">{error}</p>}

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? "Creating..." : "Create company"}
              </Button>
            </form>
          </div>
          <p className="mt-6 text-center text-sm text-muted">
            Already have a workspace?{" "}
            <Link href="/login" className="font-medium text-accent hover:text-accent-hover">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
