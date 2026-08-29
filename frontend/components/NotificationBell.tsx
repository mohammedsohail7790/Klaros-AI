"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  NotificationRow,
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
  HIGH: "bg-red-500",
  MEDIUM: "bg-amber-500",
  LOW: "bg-neutral-600",
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
        className="relative flex h-9 w-9 items-center justify-center rounded-full border border-neutral-700 hover:bg-neutral-900"
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-medium text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-96 rounded-lg border border-neutral-800 bg-neutral-950 shadow-xl">
          <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
            <span className="text-sm font-medium">Notifications</span>
            {items && items.some((n) => !n.read_at) && (
              <button onClick={handleMarkAllRead} className="text-xs text-neutral-400 underline hover:text-white">
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <p className="p-4 text-sm text-neutral-500">Loading...</p>
            ) : error ? (
              <p className="p-4 text-sm text-red-400">{error}</p>
            ) : !items || items.length === 0 ? (
              <p className="p-4 text-sm text-neutral-500">No notifications.</p>
            ) : (
              items.map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleOpenNotification(n)}
                  className={`block w-full border-b border-neutral-900 px-4 py-3 text-left hover:bg-neutral-900 ${
                    !n.read_at ? "bg-neutral-900/40" : ""
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${PRIORITY_DOT[n.priority] ?? "bg-neutral-600"}`} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-neutral-100">{n.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-neutral-400">{n.body}</p>
                      <p className="mt-1 text-[10px] text-neutral-600">{new Date(n.created_at).toLocaleString()}</p>
                    </div>
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
