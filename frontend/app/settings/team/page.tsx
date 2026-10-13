"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { Avatar } from "@/components/ui/Avatar";
import { Hero } from "@/components/ui/layout";
import { useAuth } from "@/lib/useAuth";
import { Skeleton } from "@/components/ui/Skeleton";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  TeamInvite,
  TeamMember,
  createTeamInvite,
  listTeamInvites,
  listTeamMembers,
  revokeTeamInvite,
  updateTeamMember,
} from "@/lib/api";

const ROLES = ["OWNER", "ADMIN", "MANAGER", "STAFF", "TECHNICIAN", "ACCOUNTANT", "READ_ONLY"];

export default function TeamSettingsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [invites, setInvites] = useState<TeamInvite[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("STAFF");
  const [inviting, setInviting] = useState(false);
  const [lastInviteLink, setLastInviteLink] = useState<{ url: string; emailSent: boolean } | null>(null);

  const [busyMemberId, setBusyMemberId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [membersResult, invitesResult] = await Promise.all([listTeamMembers(token), listTeamInvites(token)]);
      setMembers(membersResult.members);
      setInvites(invitesResult.invites);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load team.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !inviteEmail.trim()) return;
    setInviting(true);
    setError(null);
    setLastInviteLink(null);
    try {
      const result = await createTeamInvite(token, inviteEmail.trim(), inviteRole);
      const url = `${window.location.origin}${result.invite_url_path}`;
      setLastInviteLink({ url, emailSent: result.email_sent });
      toast.success(
        result.email_sent
          ? `Invite emailed to ${inviteEmail.trim()}.`
          : `Invite created — no email provider configured, so share this link directly.`
      );
      setInviteEmail("");
      setShowInvite(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create invite.");
    } finally {
      setInviting(false);
    }
  }

  async function handleRevoke(inviteId: string) {
    if (!token) return;
    try {
      await revokeTeamInvite(token, inviteId);
      toast.success("Invite revoked.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to revoke invite.");
    }
  }

  async function handleRoleChange(memberId: string, role: string) {
    if (!token) return;
    setBusyMemberId(memberId);
    setError(null);
    try {
      await updateTeamMember(token, memberId, { role });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update role.");
    } finally {
      setBusyMemberId(null);
    }
  }

  async function handleToggleActive(memberId: string, isActive: boolean) {
    if (!token) return;
    setBusyMemberId(memberId);
    setError(null);
    try {
      await updateTeamMember(token, memberId, { is_active: !isActive });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update member.");
    } finally {
      setBusyMemberId(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <Hero
          title="Team"
          lede="Invite people to your workspace with real, enforced roles. Every action they take goes through the same governed permission pipeline as your own."
          actions={
            <button onClick={() => setShowInvite(true)} className="klaros-btn-primary">
              Invite member
            </button>
          }
        />

        {error && (
          <div className="mb-4 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}
        {lastInviteLink && !lastInviteLink.emailSent && (
          <div className="mb-4 rounded-xl border border-warning/25 bg-warning/[0.07] p-3 text-xs text-warning">
            Invite link (share directly — no email provider configured):{" "}
            <code className="break-all">{lastInviteLink.url}</code>
          </div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : (
          <>
            <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Members</h2>
            <ul className="mb-12 divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Members">
              {members?.map((m) => (
                <li key={m.id} className="flex flex-col gap-4 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex min-w-0 items-center gap-4">
                    <Avatar name={m.full_name} />
                    <div className="min-w-0">
                      <p className="text-[16px] font-semibold tracking-tight text-foreground">
                        {m.full_name} {m.id === user?.id && <span className="ml-1 rounded-full bg-surface px-2 py-0.5 text-xs font-medium text-muted">You</span>}
                      </p>
                      <p className="truncate text-[14px] text-muted">{m.email}</p>
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-3">
                    <select
                      value={m.role}
                      aria-label={`Role for ${m.full_name}`}
                      disabled={busyMemberId === m.id}
                      onChange={(e) => handleRoleChange(m.id, e.target.value)}
                      className="rounded-full bg-surface px-4 py-1.5 text-[13px] font-medium disabled:opacity-50"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${m.is_active ? "bg-success/10 text-success" : "bg-surface text-muted"}`}>
                      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${m.is_active ? "bg-success" : "bg-muted-foreground"}`} />
                      {m.is_active ? "Active" : "Deactivated"}
                    </span>
                    <button
                      onClick={() => handleToggleActive(m.id, m.is_active)}
                      disabled={busyMemberId === m.id}
                      className="text-[14px] text-accent hover:underline disabled:opacity-50"
                    >
                      {m.is_active ? "Deactivate" : "Reactivate"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>

            {invites && invites.length > 0 && (
              <>
                <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Pending invites</h2>
                <ul className="divide-y divide-border/70 overflow-hidden rounded-3xl bg-surface-muted" aria-label="Pending invites">
                  {invites.map((i) => (
                    <li key={i.id} className="flex items-center justify-between gap-4 px-6 py-4">
                      <div className="min-w-0">
                        <p className="truncate text-[15px] font-medium text-foreground">{i.email}</p>
                        <p className="text-[13px] text-muted">{i.role} · expires {new Date(i.expires_at).toLocaleDateString()}</p>
                      </div>
                      <button onClick={() => handleRevoke(i.id)} className="rounded-full bg-danger/10 px-4 py-1.5 text-[13px] font-medium text-danger hover:bg-danger/20">
                        Revoke
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>

      {showInvite && (
        <Modal title="Invite a team member" onClose={() => setShowInvite(false)}>
          <form onSubmit={handleInvite} className="space-y-3">
            <input
              required
              type="email"
              placeholder="Email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="klaros-input"
            />
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} className="klaros-input">
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            {error && <p className="text-sm text-danger">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowInvite(false)} className="klaros-btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={inviting} className="klaros-btn-primary">
                {inviting ? "Sending..." : "Send invite"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </AppShell>
  );
}
