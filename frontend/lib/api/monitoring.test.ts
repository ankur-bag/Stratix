import assert from "node:assert/strict";
import test from "node:test";
import { isActiveScan, scanOutcome } from "./monitoring.ts";
import type { CompetitorScan } from "./types.ts";

function scan(overrides: Partial<CompetitorScan> = {}): CompetitorScan {
  return {
    id: "scan-1",
    competitor_id: "competitor-1",
    scan_type: "rescan",
    trigger: "manual",
    status: "succeeded",
    stage: "completed",
    progress_percent: 100,
    message: "Completed",
    source_count: 2,
    successful_source_count: 2,
    failed_source_count: 0,
    snapshot_count: 0,
    signal_count: 0,
    error_code: null,
    error_message: null,
    rediscover_sources: false,
    created_at: "2026-07-22T00:00:00Z",
    completed_at: "2026-07-22T00:01:00Z",
    ...overrides,
  };
}

test("recognizes active scans", () => {
  assert.equal(isActiveScan(scan({ status: "queued" })), true);
  assert.equal(isActiveScan(scan({ status: "running" })), true);
  assert.equal(isActiveScan(scan()), false);
});

test("describes baseline, no-change, change, and partial outcomes", () => {
  assert.equal(scanOutcome(scan({ scan_type: "baseline" })), "Baseline established");
  assert.equal(scanOutcome(scan()), "No meaningful changes detected");
  assert.equal(scanOutcome(scan({ signal_count: 2 })), "Changes detected");
  assert.equal(
    scanOutcome(scan({ status: "partially_succeeded" })),
    "Partially completed",
  );
});
