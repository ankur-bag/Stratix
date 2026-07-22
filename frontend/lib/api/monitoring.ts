import type { CompetitorScan } from "./types";

export function isActiveScan(scan: CompetitorScan | null | undefined): boolean {
  return scan?.status === "queued" || scan?.status === "running";
}

export function scanOutcome(scan: CompetitorScan): string {
  if (scan.status === "failed") return "Scan failed";
  if (scan.status === "partially_succeeded") return "Partially completed";
  if (scan.status !== "succeeded") return scan.message;
  if (scan.scan_type === "baseline") return "Baseline established";
  if (scan.signal_count > 0) return "Changes detected";
  return "No meaningful changes detected";
}
