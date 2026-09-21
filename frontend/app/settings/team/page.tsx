"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
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
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="font-display text-2xl text-foreground">Team</h1>
            <p className="mt-1 text-sm text-muted">
              Invite people to your workspace with real, enforced roles — every action they take goes
              through the same governed permission pipeline as your own.
            </p>
          </div>
          <button onClick={() => setShowInvite(true)} className="klaros-btn-primary">
            Invite member
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}
        {lastInviteLink && !lastInviteLink.emailSent && (
          <div className="mb-4 rounded-md border border-warning/25 bg-warning/[0.07] p-3 text-xs text-warning">
            Invite link (share directly — no email provider configured):{" "}
            <code className="break-all">{lastInviteLink.url}</code>
          </div>
        )}

        {authLoading || loading ? (
          <Skeleton />
        ) : (
          <>
            <h2 className="mb-3 font-medium">Members</h2>
            <div className="mb-8 klaros-table-wrap">
              <table className="klaros-table">
                <thead className="bg-surface text-muted">
                  <tr>
                    <th className="px-4 py-2">Name</th>
                    <th className="px-4 py-2">Email</th>
                    <th className="px-4 py-2">Role</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {members?.map((m) => (
                    <tr key={m.id} className="border-t border-border">
                      <td className="px-4 py-2">
                        {m.full_name} {m.id === user?.id && <span className="text-xs text-muted">(you)</span>}
                      </td>
                      <td className="px-4 py-2 text-muted">{m.email}</td>
                      <td className="px-4 py-2">
                        <select
                          value={m.role}
                          disabled={busyMemberId === m.id}
                          onChange={(e) => handleRoleChange(m.id, e.target.value)}
                          className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-xs disabled:opacity-50"
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        <span className={m.is_active ? "text-success" : "text-muted"}>
                          {m.is_active ? "Active" : "Deactivated"}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => handleToggleActive(m.id, m.is_active)}
                          disabled={busyMemberId === m.id}
                          className="text-xs text-muted underline hover:text-foreground disabled:opacity-50"
                        >
                          {m.is_active ? "Deactivate" : "Reactivate"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {invites && invites.length > 0 && (
              <>
                <h2 className="mb-3 font-medium">Pending invites</h2>
                <div className="klaros-table-wrap">
                  <table className="klaros-table">
                    <thead className="bg-surface text-muted">
                      <tr>
                        <th className="px-4 py-2">Email</th>
                        <th className="px-4 py-2">Role</th>
                        <th className="px-4 py-2">Expires</th>
                        <th className="px-4 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {invites.map((i) => (
                        <tr key={i.id} className="border-t border-border">
                          <td className="px-4 py-2">{i.email}</td>
                          <td className="px-4 py-2 text-muted">{i.role}</td>
                          <td className="px-4 py-2 text-muted">{new Date(i.expires_at).toLocaleDateString()}</td>
                          <td className="px-4 py-2">
                            <button
                              onClick={() => handleRevoke(i.id)}
                              className="text-xs text-danger underline hover:text-danger"
                            >
                              Revoke
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
