import type {
  StrategyMode,
  StrategyRunInput,
  StrategyRunStatus,
} from "@/lib/api/types";

export const ACTIVE_STRATEGY_STATUSES: ReadonlySet<StrategyRunStatus> = new Set([
  "queued",
  "retrieving_evidence",
  "analyzing",
  "validating",
  "cancel_requested",
]);

export function strategyStageLabel(stage: string): string {
  const labels: Record<string, string> = {
    queued: "Queued",
    preparing_company_context: "Preparing company context",
    retrieving_evidence: "Retrieving competitor evidence",
    analyzing: "Analyzing product, pricing, positioning, and risk signals",
    validating_citations: "Validating evidence citations",
    preparing_report: "Preparing strategic report",
    awaiting_retry: "Waiting to retry",
    cancel_requested: "Cancelling",
    cancelled: "Cancelled",
    completed: "Completed",
    failed: "Failed",
  };
  return labels[stage] ?? stage.replaceAll("_", " ");
}

export function validateStrategyInput(
  input: StrategyRunInput,
  maxCompetitors = 10,
): string | null {
  const query = input.query.trim();
  if (input.mode !== "executive_brief" && !query) {
    return "Enter a strategic question.";
  }
  if (input.mode === "compare" && input.competitor_ids.length < 2) {
    return "Select at least two competitors to compare.";
  }
  if (input.competitor_ids.length > maxCompetitors) {
    return `Select no more than ${maxCompetitors} competitors.`;
  }
  const start = Date.parse(input.date_from);
  const end = Date.parse(input.date_to);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) {
    return "Choose a valid date range.";
  }
  const maximum = 1095 * 24 * 60 * 60 * 1000;
  if (end - start > maximum) {
    return "Choose a date range of three years or less.";
  }
  return null;
}

export function strategyModeLabel(mode: StrategyMode): string {
  return {
    ask: "Ask a question",
    compare: "Compare competitors",
    executive_brief: "Executive brief",
  }[mode];
}

export function dateInputValue(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function toUtcBoundary(value: string, endOfDay = false): string {
  return new Date(`${value}T${endOfDay ? "23:59:59.999" : "00:00:00.000"}Z`).toISOString();
}

export function shouldPollStrategyRun(
  status: StrategyRunStatus,
  documentHidden: boolean,
): boolean {
  return ACTIVE_STRATEGY_STATUSES.has(status) && !documentHidden;
}

export function toggleStrategyCompetitor(
  selected: string[],
  competitorId: string,
  maximum = 10,
): string[] {
  if (selected.includes(competitorId)) {
    return selected.filter((item) => item !== competitorId);
  }
  return selected.length < maximum ? [...selected, competitorId] : selected;
}
