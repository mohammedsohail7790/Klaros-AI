"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, register } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import GradientBackdrop from "@/components/GradientBackdrop";

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
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center overflow-hidden px-6 py-12">
      <GradientBackdrop />
      <div className="w-full max-w-sm">
        <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground">
          Klaros
        </Link>
        <div className="klaros-glass rounded-2xl p-7">
          <h1 className="font-display text-2xl text-foreground">Create your company</h1>
          <p className="mt-1 text-sm text-muted">Set up your Klaros workspace in a couple of minutes.</p>

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
    </main>
  );
}
