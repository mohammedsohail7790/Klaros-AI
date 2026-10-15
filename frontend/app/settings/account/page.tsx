"use client";

import { useState } from "react";
import { UserCog } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { changePassword } from "@/lib/api";
import { setSession } from "@/lib/session";
import { friendlyAuthError, passwordHint } from "@/lib/authErrors";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { BusinessNameCard } from "@/components/BusinessNameCard";

const ROLE_LABEL: Record<string, string> = { OWNER: "Owner", ADMIN: "Admin", MANAGER: "Manager", STAFF: "Staff", TECHNICIAN: "Technician" };

/** Who you are signed in as, and the one security action every account needs: changing the password. Changing it signs every other device out. */
export default function AccountPage() {
  const { token, user, loading } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const hint = passwordHint(next);
  const mismatch = confirm.length > 0 && confirm !== next;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !hint.ok || mismatch || confirm === "") return;
    setSaving(true);
    setError(null);
    setDone(false);
    try {
      setSession(await changePassword(token, current, next));
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(friendlyAuthError(err, "Unable to change the password."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-3xl px-6 py-8">
        <PageHeader title="Account & security" description="Your sign-in details and password." icon={UserCog} />

        <section aria-label="Your account" className="klaros-card mb-6 p-5 sm:p-6">
          {loading || !user ? (
            <p className="text-sm text-muted">Loading...</p>
          ) : (
            <dl className="grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">Name</dt>
                <dd className="mt-1 text-foreground">{user.full_name}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Email</dt>
                <dd className="mt-1 break-all text-foreground">{user.email}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Role</dt>
                <dd className="mt-1 text-foreground">{ROLE_LABEL[user.role] ?? user.role}</dd>
              </div>
            </dl>
          )}
        </section>

        <BusinessNameCard token={token} role={user?.role} />

        <section aria-label="Change password" className="klaros-card p-5 sm:p-6">
          <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Change password</h2>
          <p className="mt-1 text-sm text-muted">After you change it, every other device is signed out. This one stays signed in.</p>
          <form onSubmit={submit} className="mt-4 max-w-sm space-y-4">
            {done && <Alert variant="success">Your password was changed.</Alert>}
            {error && <Alert variant="danger">{error}</Alert>}
            <Field label="Current password">
              <Input required type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
            </Field>
            <div>
              <Field label="New password">
                <Input required type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
              </Field>
              <p className={`mt-1.5 text-xs ${hint.ok ? "text-success" : "text-muted"}`}>{hint.text}</p>
            </div>
            <div>
              <Field label="Repeat the new password">
                <Input required type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              </Field>
              {mismatch && <p className="mt-1.5 text-xs text-danger">The two passwords don't match.</p>}
            </div>
            <Button type="submit" disabled={saving || !token || current === "" || !hint.ok || mismatch || confirm === ""}>
              {saving ? "Saving..." : "Change password"}
            </Button>
          </form>
        </section>
      </div>
    </AppShell>
  );
}
