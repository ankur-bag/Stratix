import type {
  BackgroundJob,
  MonitoringCadence,
  MonitoringSchedule,
  Notification,
} from "./types";

export const monitoringCadences: Array<{
  value: MonitoringCadence;
  label: string;
}> = [
  { value: "manual", label: "Manual only" },
  { value: "daily", label: "Daily" },
  { value: "every_3_days", label: "Every 3 days" },
  { value: "weekly", label: "Weekly" },
];

export function monitoringState(schedule: MonitoringSchedule): string {
  if (!schedule.is_enabled || schedule.cadence === "manual") return "Paused";
  if (schedule.degraded_reason) return "Needs attention";
  return "Enabled";
}

export function monitoringScheduleUpdate(cadence: MonitoringCadence): {
  cadence: MonitoringCadence;
  is_enabled: boolean;
} {
  return { cadence, is_enabled: cadence !== "manual" };
}

export function durableJobMessage(job: BackgroundJob | null): string {
  if (!job) return "The scan was accepted.";
  if (job.status === "queued" || job.status === "retry_scheduled") {
    return "Queued safely. A worker will start this scan.";
  }
  if (job.status === "dead_lettered") {
    return "The scan exhausted its retries. Open job recovery to try again.";
  }
  return job.message;
}

export function notificationBadge(unreadCount: number): string | null {
  if (unreadCount <= 0) return null;
  return unreadCount > 99 ? "99+" : String(unreadCount);
}

export function organizationStateKey(
  organizationId: string | null | undefined,
  scope: string,
): string {
  return `${organizationId ?? "no-organization"}:${scope}`;
}

export function shouldPollNotifications(
  visibilityState: DocumentVisibilityState,
): boolean {
  return visibilityState === "visible";
}

export function mergeNotifications(
  current: Notification[],
  incoming: Notification[],
): Notification[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
  );
}
