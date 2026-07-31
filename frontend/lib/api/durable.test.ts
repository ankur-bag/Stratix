import assert from "node:assert/strict";
import test from "node:test";
import {
  durableJobMessage,
  mergeNotifications,
  monitoringScheduleUpdate,
  monitoringState,
  notificationBadge,
  organizationStateKey,
  shouldPollNotifications,
} from "./durable.ts";
import type { BackgroundJob, MonitoringSchedule, Notification } from "./types.ts";

const schedule: MonitoringSchedule = {
  id: "schedule",
  competitor_id: "competitor",
  cadence: "weekly",
  timezone: "UTC",
  is_enabled: true,
  next_run_at: "2026-08-07T00:00:00Z",
  last_scheduled_at: null,
  last_job_id: null,
  last_success_at: null,
  last_failure_at: null,
  degraded_reason: null,
};

test("monitoring state reflects enabled, paused, and degraded schedules", () => {
  assert.equal(monitoringState(schedule), "Enabled");
  assert.equal(monitoringState({ ...schedule, is_enabled: false }), "Paused");
  assert.equal(monitoringState({ ...schedule, degraded_reason: "QUEUE_FULL" }), "Needs attention");
});

test("durable accepted scans explain the worker boundary", () => {
  const job = {
    status: "queued",
    message: "Background job is queued.",
  } as BackgroundJob;
  assert.match(durableJobMessage(job), /Queued safely/);
  assert.equal(durableJobMessage(null), "The scan was accepted.");
  assert.match(
    durableJobMessage({
      ...job,
      status: "dead_lettered",
    } as BackgroundJob),
    /exhausted its retries/,
  );
});

test("cadence selection enables automatic schedules except manual mode", () => {
  assert.deepEqual(monitoringScheduleUpdate("daily"), {
    cadence: "daily",
    is_enabled: true,
  });
  assert.deepEqual(monitoringScheduleUpdate("manual"), {
    cadence: "manual",
    is_enabled: false,
  });
});

test("unread badges are bounded and organization state keys change on switch", () => {
  assert.equal(notificationBadge(0), null);
  assert.equal(notificationBadge(4), "4");
  assert.equal(notificationBadge(120), "99+");
  assert.notEqual(
    organizationStateKey("org-a", "notifications"),
    organizationStateKey("org-b", "notifications"),
  );
});

test("notification polling pauses while hidden", () => {
  assert.equal(shouldPollNotifications("visible"), true);
  assert.equal(shouldPollNotifications("hidden"), false);
});

test("notification merges are deduplicated and newest first", () => {
  const first = {
    id: "one",
    created_at: "2026-01-01T00:00:00Z",
  } as Notification;
  const second = {
    id: "two",
    created_at: "2026-01-02T00:00:00Z",
  } as Notification;
  assert.deepEqual(
    mergeNotifications([first], [first, second]).map((item) => item.id),
    ["two", "one"],
  );
});
