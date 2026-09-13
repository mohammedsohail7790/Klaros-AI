"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, X } from "lucide-react";
import {
  ApiError,
  NotificationRow,
  dismissNotification,
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/api";

const ENTITY_LINK: Record<string, (id: string) => string> = {
  approval_request: () => "/approvals",
  morning_brief: () => "/morning-brief",
  customer: (id) => `/customers/${id}`,
  lead: (id) => `/leads/${id}`,
  job: (id) => `/jobs/${id}`,
  invoice: (id) => `/finance/invoices/${id}`,
};

const PRIORITY_DOT: Record<string, string> = {
  HIGH: "bg-danger",
  MEDIUM: "bg-warning",
  LOW: "bg-muted-foreground",
};

export default function NotificationBell({ token }: { token: string | null }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<NotificationRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const refreshCount = useCallback(async () => {
    if (!token) return;
    try {
      const result = await getUnreadNotificationCount(token);
      setUnread(result.unread_count);
    } catch {
      // Silent — the bell just shows no badge if this fails; opening the
      // dropdown surfaces the real error state.
    }
  }, [token]);

  useEffect(() => {
    refreshCount();
    const interval = setInterval(refreshCount, 30000);
    return () => clearInterval(interval);
  }, [refreshCount]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  async function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (next && token) {
      setLoading(true);
      setError(null);
      try {
        const result = await listNotifications(token, false, 20);
        setItems(result.notifications);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Unable to load notifications.");
      } finally {
        setLoading(false);
      }
    }
  }

  async function handleMarkAllRead() {
    if (!token) return;
    await markAllNotificationsRead(token);
    setItems((prev) => prev?.map((n) => ({ ...n, status: "READ", read_at: new Date().toISOString() })) ?? null);
    setUnread(0);
  }

  async function handleDismiss(e: React.MouseEvent, n: NotificationRow) {
    e.stopPropagation();
    if (!token) return;
    await dismissNotification(token, n.id);
    setItems((prev) => prev?.filter((x) => x.id !== n.id) ?? null);
    if (!n.read_at) {
      setUnread((c) => Math.max(0, c - 1));
    }
  }

  async function handleOpenNotification(n: NotificationRow) {
    if (!token) return;
    if (!n.read_at) {
      await markNotificationRead(token, n.id);
      setItems((prev) => prev?.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString(), status: "READ" } : x)) ?? null);
      setUnread((c) => Math.max(0, c - 1));
    }
    setOpen(false);
    const linkFn = n.entity_type ? ENTITY_LINK[n.entity_type] : undefined;
    if (linkFn && n.entity_id) {
      router.push(linkFn(n.entity_id));
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={toggleOpen}
        aria-label="Notifications"
        className="relative flex h-9 w-9 items-center justify-center rounded-full border border-border text-muted transition-colors hover:bg-surface-muted hover:text-foreground"
      >
        <Bell className="h-4 w-4" strokeWidth={2} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-medium text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-96 rounded-xl border border-border bg-surface shadow-popover">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <span className="text-sm font-semibold text-foreground">Notifications</span>
            {items && items.some((n) => !n.read_at) && (
              <button onClick={handleMarkAllRead} className="text-xs font-medium text-accent hover:text-accent-hover">
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <p className="p-4 text-sm text-muted">Loading...</p>
            ) : error ? (
              <p className="p-4 text-sm text-danger">{error}</p>
            ) : !items || items.length === 0 ? (
              <p className="p-4 text-sm text-muted">No notifications.</p>
            ) : (
              items.map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleOpenNotification(n)}
                  className={`group block w-full border-b border-border px-4 py-3 text-left transition-colors hover:bg-surface-muted ${
                    !n.read_at ? "bg-accent-soft/40" : ""
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${PRIORITY_DOT[n.priority] ?? "bg-muted-foreground"}`} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">{n.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted">{n.body}</p>
                      <p className="mt-1 text-[10px] text-muted-foreground">{new Date(n.created_at).toLocaleString()}</p>
                    </div>
                    <span
                      role="button"
                      aria-label="Dismiss notification"
                      onClick={(e) => handleDismiss(e, n)}
                      className="shrink-0 rounded-full p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-surface hover:text-foreground group-hover:opacity-100"
                    >
                      <X className="h-3.5 w-3.5" strokeWidth={2} />
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
