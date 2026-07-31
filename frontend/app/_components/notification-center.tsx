"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import {
  mergeNotifications,
  notificationBadge,
  shouldPollNotifications,
} from "@/lib/api/durable";
import type { Notification } from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";

const pollIntervalMilliseconds = 45_000;

export function NotificationCenter() {
  const api = useAuthenticatedApi();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const badge = notificationBadge(unreadCount);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;

    function scheduleNext() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(refresh, pollIntervalMilliseconds);
    }

    async function refresh() {
      if (!shouldPollNotifications(document.visibilityState)) return;
      if (timer) {
        clearTimeout(timer);
        timer = undefined;
      }
      controller?.abort();
      controller = new AbortController();
      try {
        const [page, count] = await Promise.all([
          api.listNotifications(controller.signal, { limit: 20 }),
          api.getNotificationUnreadCount(controller.signal),
        ]);
        if (cancelled) return;
        setItems((current) => mergeNotifications(current, page.items));
        setUnreadCount(count.unread_count);
        setError(null);
      } catch (requestError) {
        if (
          !cancelled &&
          !(requestError instanceof ApiError && requestError.kind === "cancelled")
        ) {
          setError(
            requestError instanceof ApiError
              ? requestError.message
              : "Notifications could not be refreshed.",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          if (shouldPollNotifications(document.visibilityState)) scheduleNext();
        }
      }
    }

    function handleVisibilityChange() {
      if (shouldPollNotifications(document.visibilityState)) void refresh();
      else {
        controller?.abort();
        if (timer) {
          clearTimeout(timer);
          timer = undefined;
        }
      }
    }

    void refresh();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [api]);

  useEffect(() => {
    if (!open) return;
    function dismiss(event: MouseEvent) {
      if (!panelRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function dismissWithKeyboard(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", dismissWithKeyboard);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", dismissWithKeyboard);
    };
  }, [open]);

  async function markRead(notification: Notification) {
    if (notification.is_read) return;
    try {
      const updated = await api.markNotificationRead(notification.id);
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setUnreadCount((count) => Math.max(0, count - 1));
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "The notification could not be marked as read.",
      );
    }
  }

  async function markAllRead() {
    try {
      await api.markAllNotificationsRead();
      setItems((current) =>
        current.map((item) => ({ ...item, is_read: true })),
      );
      setUnreadCount(0);
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Notifications could not be updated.",
      );
    }
  }

  return (
    <div className="relative" ref={panelRef}>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={
          unreadCount
            ? `Notifications, ${unreadCount} unread`
            : "Notifications"
        }
        className="relative rounded-full border border-black/15 px-3 py-2 text-sm font-medium transition hover:border-black/35 focus-visible:outline-2 focus-visible:outline-offset-2 dark:border-white/15 dark:hover:border-white/35"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        Alerts
        {badge ? (
          <span className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-red-600 px-1.5 py-0.5 text-[11px] font-semibold leading-4 text-white">
            {badge}
          </span>
        ) : null}
      </button>

      {open ? (
        <section
          aria-label="Notifications"
          className="absolute right-0 top-12 z-50 w-[min(24rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-black/10 bg-white shadow-[0_18px_45px_rgba(0,0,0,0.16)] dark:border-white/10 dark:bg-zinc-950"
          role="dialog"
        >
          <header className="flex items-center justify-between gap-4 border-b border-black/10 px-5 py-4 dark:border-white/10">
            <div>
              <h2 className="font-semibold">Notifications</h2>
              <p className="mt-0.5 text-xs text-zinc-500">
                Durable scan and processing updates
              </p>
            </div>
            {unreadCount ? (
              <button
                className="text-xs font-semibold text-zinc-600 underline-offset-4 hover:underline dark:text-zinc-300"
                onClick={markAllRead}
                type="button"
              >
                Mark all read
              </button>
            ) : null}
          </header>

          <div className="max-h-[28rem] overflow-y-auto">
            {loading ? (
              <p className="px-5 py-8 text-sm text-zinc-500">
                Loading notifications…
              </p>
            ) : error && !items.length ? (
              <p className="px-5 py-8 text-sm text-red-700 dark:text-red-300" role="alert">
                {error}
              </p>
            ) : !items.length ? (
              <div className="px-5 py-9">
                <p className="font-medium">You’re all caught up</p>
                <p className="mt-1 text-sm leading-6 text-zinc-500">
                  New signals and final job failures will appear here.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-black/10 dark:divide-white/10">
                {items.map((notification) => (
                  <li
                    className={
                      notification.is_read
                        ? "bg-white dark:bg-zinc-950"
                        : "bg-zinc-50 dark:bg-zinc-900"
                    }
                    key={notification.id}
                  >
                    <Link
                      className="block px-5 py-4 outline-none transition hover:bg-black/[0.035] focus-visible:bg-black/[0.055] dark:hover:bg-white/[0.045] dark:focus-visible:bg-white/[0.065]"
                      href={notification.action_url ?? "/dashboard"}
                      onClick={() => {
                        void markRead(notification);
                        setOpen(false);
                      }}
                    >
                      <div className="flex items-start gap-3">
                        <span
                          aria-hidden="true"
                          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                            notification.severity === "error"
                              ? "bg-red-600"
                              : notification.severity === "warning"
                                ? "bg-amber-500"
                                : "bg-blue-600"
                          }`}
                        />
                        <div className="min-w-0">
                          <p className="text-sm font-semibold">
                            {notification.title}
                          </p>
                          <p className="mt-1 text-sm leading-5 text-zinc-600 dark:text-zinc-400">
                            {notification.message}
                          </p>
                          <p className="mt-2 text-xs text-zinc-500">
                            {formatDate(notification.created_at)}
                          </p>
                        </div>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {error && items.length ? (
            <p className="border-t border-black/10 px-5 py-3 text-xs text-red-700 dark:border-white/10 dark:text-red-300" role="alert">
              {error}
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
