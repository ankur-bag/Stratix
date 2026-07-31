"use client";

import { useOrganization } from "@clerk/nextjs";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type {
  Competitor,
  EvidenceExcerpt,
  StrategicClaim,
  StrategicComparison,
  StrategicReport,
  StrategicRecommendation,
  StrategyMode,
  StrategyRun,
} from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";
import {
  ACTIVE_STRATEGY_STATUSES,
  dateInputValue,
  shouldPollStrategyRun,
  strategyModeLabel,
  strategyStageLabel,
  toggleStrategyCompetitor,
  toUtcBoundary,
  validateStrategyInput,
} from "@/lib/strategy";

const suggestions: Record<StrategyMode, string[]> = {
  ask: [
    "What should we prioritize based on recent competitor changes?",
    "Where are competitors leaving meaningful product gaps?",
  ],
  compare: [
    "Compare their AI positioning and public pricing movement.",
    "Which selected competitor is expanding product scope fastest?",
  ],
  executive_brief: [
    "Summarize the most strategically important changes in this period.",
    "Prepare an evidence-grounded competitor intelligence brief.",
  ],
};

export function StrategyWorkspace() {
  const { organization } = useOrganization();
  return (
    <OrganizationStrategyWorkspace
      key={organization?.id ?? "no-organization"}
    />
  );
}

function OrganizationStrategyWorkspace() {
  const api = useAuthenticatedApi();
  const today = useMemo(() => new Date(), []);
  const ninetyDaysAgo = useMemo(
    () => new Date(today.getTime() - 90 * 24 * 60 * 60 * 1000),
    [today],
  );
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [runs, setRuns] = useState<StrategyRun[]>([]);
  const [reports, setReports] = useState<StrategicReport[]>([]);
  const [activeRun, setActiveRun] = useState<StrategyRun | null>(null);
  const [report, setReport] = useState<StrategicReport | null>(null);
  const [mode, setMode] = useState<StrategyMode>("ask");
  const [query, setQuery] = useState("");
  const [selectedCompetitors, setSelectedCompetitors] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState(dateInputValue(ninetyDaysAgo));
  const [dateTo, setDateTo] = useState(dateInputValue(today));
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [citation, setCitation] = useState<EvidenceExcerpt | null>(null);
  const [citationLoading, setCitationLoading] = useState(false);
  const [feedback, setFeedback] = useState<"helpful" | "not_helpful" | null>(null);
  const controllers = useRef(new Set<AbortController>());

  useEffect(() => {
    const controller = new AbortController();
    const managedControllers = controllers.current;
    managedControllers.add(controller);
    let active = true;
    async function load() {
      try {
        const [competitorData, runData, reportData] = await Promise.all([
          api.listCompetitors(controller.signal),
          api.listStrategyRuns(controller.signal),
          api.listStrategicReports(controller.signal),
        ]);
        if (!active) return;
        setCompetitors(competitorData);
        setRuns(runData.items);
        setReports(reportData.items);
        const current = runData.items.find((item) =>
          ACTIVE_STRATEGY_STATUSES.has(item.status),
        );
        setActiveRun(current ?? null);
        const requestedReportId = new URLSearchParams(window.location.search).get("report");
        const initialReport =
          reportData.items.find((item) => item.id === requestedReportId) ??
          reportData.items[0] ??
          null;
        setReport(initialReport);
      } catch (loadError) {
        if (active && !isCancelled(loadError)) {
          setError(messageFor(loadError, "Strategic analysis could not be loaded."));
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      managedControllers.forEach((item) => item.abort());
      managedControllers.clear();
    };
  }, [api]);

  const activeRunId = activeRun?.id;
  const activeRunStatus = activeRun?.status;
  useEffect(() => {
    if (!activeRunId || !activeRunStatus || !shouldPollStrategyRun(activeRunStatus, false)) return;
    const runId = activeRunId;
    const runStatus = activeRunStatus;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    async function poll() {
      if (stopped) return;
      if (!shouldPollStrategyRun(runStatus, document.hidden)) {
        timer = setTimeout(poll, 8000);
        return;
      }
      controller = new AbortController();
      controllers.current.add(controller);
      try {
        const response = await api.getStrategyRun(runId, controller.signal);
        if (stopped) return;
        setActiveRun(response.run);
        setRuns((items) => upsertRun(items, response.run));
        if (!ACTIVE_STRATEGY_STATUSES.has(response.run.status)) {
          if (response.run.report_id) {
            const [completedReport, history] = await Promise.all([
              api.getStrategicReport(response.run.report_id, controller.signal),
              api.listStrategicReports(controller.signal),
            ]);
            if (!stopped) {
              setReport(completedReport);
              setReports(history.items);
              setFeedback(null);
            }
          }
          return;
        }
      } catch (pollError) {
        if (!stopped && !isCancelled(pollError)) {
          setError(messageFor(pollError, "Run progress could not be refreshed."));
          timer = setTimeout(poll, 8000);
          return;
        }
      } finally {
        if (controller) controllers.current.delete(controller);
      }
      timer = setTimeout(poll, 2500);
    }
    timer = setTimeout(poll, 1000);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
    };
  }, [activeRunId, activeRunStatus, api]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || (activeRun && ACTIVE_STRATEGY_STATUSES.has(activeRun.status))) return;
    const input = {
      mode,
      query: query.trim(),
      competitor_ids: selectedCompetitors,
      date_from: toUtcBoundary(dateFrom),
      date_to: toUtcBoundary(dateTo, true),
    };
    const validationError = validateStrategyInput(input);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.createStrategyRun(input);
      setActiveRun(response.run);
      setRuns((items) => upsertRun(items, response.run));
    } catch (submitError) {
      setError(messageFor(submitError, "Strategic analysis could not be queued."));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelRun() {
    if (!activeRun) return;
    try {
      const response = await api.cancelStrategyRun(activeRun.id);
      setActiveRun(response.run);
      setRuns((items) => upsertRun(items, response.run));
    } catch (cancelError) {
      setError(messageFor(cancelError, "The strategic run could not be cancelled."));
    }
  }

  async function rerunReport(previous: StrategicReport) {
    if (submitting || runActive) return;
    const previousStart = Date.parse(previous.date_from);
    const previousEnd = Date.parse(previous.date_to);
    const end = new Date();
    const lookback = Math.max(0, previousEnd - previousStart);
    const start = new Date(end.getTime() - lookback);
    const input = {
      mode: previous.mode,
      query:
        previous.mode === "executive_brief"
          ? ""
          : `Update "${previous.title}" using the latest verified evidence.`,
      competitor_ids: previous.competitor_ids,
      date_from: start.toISOString(),
      date_to: end.toISOString(),
    };
    const validationError = validateStrategyInput(input);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.createStrategyRun(input);
      setActiveRun(response.run);
      setRuns((items) => upsertRun(items, response.run));
    } catch (rerunError) {
      setError(messageFor(rerunError, "The report could not be rerun."));
    } finally {
      setSubmitting(false);
    }
  }

  async function retryRun(runId: string) {
    if (submitting || runActive) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.retryStrategyRun(runId);
      setActiveRun(response.run);
      setRuns((items) => upsertRun(items, response.run));
    } catch (retryError) {
      setError(messageFor(retryError, "The failed run could not be retried."));
    } finally {
      setSubmitting(false);
    }
  }

  async function openReport(reportId: string) {
    setError(null);
    setCitation(null);
    try {
      const selected = await api.getStrategicReport(reportId);
      setReport(selected);
      setFeedback(null);
      window.history.replaceState(null, "", `/strategy?report=${encodeURIComponent(reportId)}`);
    } catch (openError) {
      setError(messageFor(openError, "The strategic report could not be opened."));
    }
  }

  async function openCitation(evidenceId: string) {
    if (!report) return;
    setCitation(null);
    setCitationLoading(true);
    const controller = new AbortController();
    controllers.current.add(controller);
    try {
      setCitation(
        await api.getStrategicCitation(report.id, evidenceId, controller.signal),
      );
    } catch (citationError) {
      if (!isCancelled(citationError)) {
        setError(
          messageFor(citationError, "This supporting evidence is no longer available."),
        );
      }
    } finally {
      controllers.current.delete(controller);
      setCitationLoading(false);
    }
  }

  async function sendFeedback(value: "helpful" | "not_helpful") {
    if (!report) return;
    try {
      await api.submitStrategicFeedback(report.id, value);
      setFeedback(value);
    } catch (feedbackError) {
      setError(messageFor(feedbackError, "Feedback could not be saved."));
    }
  }

  function toggleCompetitor(id: string) {
    setSelectedCompetitors((items) => toggleStrategyCompetitor(items, id));
  }

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-14 sm:px-6">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Loading strategic workspace…
        </p>
      </main>
    );
  }

  const competitorNames = new Map(competitors.map((item) => [item.id, item.name]));
  const runActive = Boolean(
    activeRun && ACTIVE_STRATEGY_STATUSES.has(activeRun.status),
  );

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-10 sm:px-6 sm:py-14">
      <header className="border-b border-black/10 pb-9 dark:border-white/10">
        <p className="text-sm font-medium text-zinc-600 dark:text-zinc-400">
          Strategic intelligence
        </p>
        <h1 className="mt-2 max-w-4xl text-4xl font-semibold tracking-[-0.035em] sm:text-5xl">
          Turn verified competitor evidence into a decision brief.
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-zinc-600 dark:text-zinc-400">
          Ask a focused question, compare approved competitors, or prepare an
          executive brief. Every substantive conclusion remains traceable.
        </p>
      </header>

      {error ? (
        <div
          className="mt-6 flex items-start justify-between gap-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-900 dark:bg-red-950/40 dark:text-red-100"
          role="alert"
        >
          <p>{error}</p>
          <button aria-label="Dismiss error" onClick={() => setError(null)} type="button">
            ×
          </button>
        </div>
      ) : null}

      <section className="mt-8 grid gap-8 border-b border-black/10 pb-10 dark:border-white/10 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <form onSubmit={submit}>
          <div aria-label="Analysis mode" className="flex flex-wrap gap-2" role="group">
            {(["ask", "compare", "executive_brief"] as const).map((item) => (
              <button
                aria-pressed={mode === item}
                className={`min-h-10 rounded-full px-4 text-sm font-medium transition ${
                  mode === item
                    ? "bg-foreground text-background"
                    : "border border-black/15 hover:border-black/35 dark:border-white/20 dark:hover:border-white/40"
                }`}
                key={item}
                onClick={() => setMode(item)}
                type="button"
              >
                {strategyModeLabel(item)}
              </button>
            ))}
          </div>

          <label className="mt-6 block text-sm font-semibold" htmlFor="strategy-query">
            {mode === "executive_brief" ? "Brief focus (optional)" : "Strategic question"}
          </label>
          <textarea
            className="mt-2 min-h-32 w-full resize-y rounded-xl border border-black/15 bg-transparent px-4 py-3 text-base leading-7 outline-none transition placeholder:text-zinc-500 focus:border-black focus:ring-2 focus:ring-black/10 dark:border-white/20 dark:focus:border-white dark:focus:ring-white/10"
            disabled={runActive}
            id="strategy-query"
            maxLength={2000}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="What should we prioritize based on recent competitor changes?"
            value={query}
          />
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2">
            {suggestions[mode].map((item) => (
              <button
                className="text-left text-xs text-zinc-600 underline decoration-zinc-400 underline-offset-4 transition hover:text-foreground dark:text-zinc-400"
                key={item}
                onClick={() => setQuery(item)}
                type="button"
              >
                {item}
              </button>
            ))}
          </div>

          <fieldset className="mt-7">
            <legend className="text-sm font-semibold">
              Competitors{" "}
              <span className="font-normal text-zinc-500">
                ({selectedCompetitors.length}/10 selected)
              </span>
            </legend>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {competitors.map((item) => (
                <label
                  className="flex min-h-11 items-center gap-3 rounded-lg border border-black/10 px-3 text-sm transition hover:border-black/30 dark:border-white/15 dark:hover:border-white/35"
                  key={item.id}
                >
                  <input
                    checked={selectedCompetitors.includes(item.id)}
                    disabled={
                      runActive ||
                      (!selectedCompetitors.includes(item.id) &&
                        selectedCompetitors.length >= 10)
                    }
                    onChange={() => toggleCompetitor(item.id)}
                    type="checkbox"
                  />
                  <span className="truncate">{item.name}</span>
                </label>
              ))}
            </div>
            {!competitors.length ? (
              <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">
                Approve and scan competitors before generating a strategic report.
              </p>
            ) : null}
          </fieldset>

          <div className="mt-7 flex flex-wrap gap-4">
            <label className="text-sm">
              <span className="mb-2 block font-semibold">From</span>
              <input
                className="min-h-11 rounded-lg border border-black/15 bg-background px-3 dark:border-white/20"
                disabled={runActive}
                max={dateTo}
                onChange={(event) => setDateFrom(event.target.value)}
                type="date"
                value={dateFrom}
              />
            </label>
            <label className="text-sm">
              <span className="mb-2 block font-semibold">To</span>
              <input
                className="min-h-11 rounded-lg border border-black/15 bg-background px-3 dark:border-white/20"
                disabled={runActive}
                max={dateInputValue(today)}
                min={dateFrom}
                onChange={(event) => setDateTo(event.target.value)}
                type="date"
                value={dateTo}
              />
            </label>
          </div>

          <button
            className="mt-7 min-h-12 rounded-full bg-foreground px-6 font-semibold text-background transition hover:opacity-85 disabled:opacity-45"
            disabled={submitting || runActive || !competitors.length}
            type="submit"
          >
            {submitting ? "Queuing analysis…" : "Prepare strategic report"}
          </button>
        </form>

        <aside>
          <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-zinc-500">
            Run status
          </h2>
          {activeRun ? (
            <RunStatus run={activeRun} onCancel={cancelRun} />
          ) : (
            <div className="mt-4 border-t border-black/10 pt-5 dark:border-white/10">
              <p className="font-medium">Ready for a focused question</p>
              <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                Analysis runs in the durable worker. You can leave this page and
                return when the report notification arrives.
              </p>
            </div>
          )}
        </aside>
      </section>

      {report ? (
        <ReportView
          competitorNames={competitorNames}
          feedback={feedback}
          onCitation={openCitation}
          onFeedback={sendFeedback}
          report={report}
        />
      ) : (
        <section className="py-14">
          <h2 className="text-2xl font-semibold tracking-tight">No report selected</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
            Completed reports will separate verified observations, supported
            inferences, and advisory recommendations.
          </p>
        </section>
      )}

      <ReportHistory
        competitorNames={competitorNames}
        disabled={submitting || runActive}
        onOpen={openReport}
        onRerun={rerunReport}
        onRetry={retryRun}
        reports={reports}
        runs={runs}
      />

      {(citationLoading || citation) && report ? (
        <CitationDrawer
          citation={citation}
          loading={citationLoading}
          onClose={() => setCitation(null)}
        />
      ) : null}
    </main>
  );
}

function RunStatus({ run, onCancel }: { run: StrategyRun; onCancel: () => void }) {
  const active = ACTIVE_STRATEGY_STATUSES.has(run.status);
  return (
    <div className="mt-4 border-t border-black/10 pt-5 dark:border-white/10">
      <div className="flex items-center justify-between gap-4">
        <span className="font-medium">{strategyStageLabel(run.stage)}</span>
        <span className="text-sm tabular-nums text-zinc-500">{run.progress_percent}%</span>
      </div>
      <div
        aria-label={`${run.progress_percent}% complete`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={run.progress_percent}
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-900"
        role="progressbar"
      >
        <div
          className="h-full rounded-full bg-zinc-950 transition-[width] duration-500 dark:bg-zinc-100"
          style={{ width: `${run.progress_percent}%` }}
        />
      </div>
      <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
        {run.message}
      </p>
      {active ? (
        <button
          className="mt-4 text-sm font-semibold underline decoration-zinc-400 underline-offset-4"
          onClick={onCancel}
          type="button"
        >
          Cancel run
        </button>
      ) : null}
      {run.error_message ? (
        <p className="mt-3 text-sm text-red-700 dark:text-red-300">{run.error_message}</p>
      ) : null}
    </div>
  );
}

function ReportView({
  report,
  competitorNames,
  onCitation,
  feedback,
  onFeedback,
}: {
  report: StrategicReport;
  competitorNames: Map<string, string>;
  onCitation: (id: string) => void;
  feedback: "helpful" | "not_helpful" | null;
  onFeedback: (value: "helpful" | "not_helpful") => void;
}) {
  return (
    <article className="py-12">
      {report.deterministic_fallback ? (
        <div className="mb-8 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100">
          This is a deterministic evidence summary. No AI strategic
          recommendations were generated.
        </div>
      ) : null}
      <div className="grid gap-8 border-b border-black/10 pb-9 dark:border-white/10 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div>
          <p className="text-sm text-zinc-500">
            {strategyModeLabel(report.mode)} ·{" "}
            {new Date(report.analysis_as_of).toLocaleString()}
          </p>
          <h2 className="mt-2 text-3xl font-semibold tracking-[-0.025em]">
            {report.title}
          </h2>
          <p className="mt-5 max-w-4xl text-lg leading-8">{report.executive_summary}</p>
        </div>
        <div className="min-w-52 text-sm text-zinc-600 dark:text-zinc-400">
          <p>{report.evidence_coverage.cited_evidence_count} cited records</p>
          <p className="mt-1">{report.evidence_coverage.competitor_count} competitors</p>
          <p className="mt-1">{report.evidence_coverage.source_type_count} source types</p>
        </div>
      </div>

      <div className="mt-10 grid gap-12 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
        <div className="space-y-12">
          <ClaimSection
            competitorNames={competitorNames}
            items={report.situation_assessment}
            onCitation={onCitation}
            title="Verified observations"
          />
          <ClaimSection
            competitorNames={competitorNames}
            items={report.inferences}
            onCitation={onCitation}
            title="Supported inferences"
          />
          <ComparisonSection
            competitorNames={competitorNames}
            items={report.competitor_comparisons}
            onCitation={onCitation}
          />
          <RecommendationSection
            competitorNames={competitorNames}
            items={report.recommendations}
            onCitation={onCitation}
          />
        </div>
        <aside className="space-y-10">
          <ClaimSection
            competitorNames={competitorNames}
            items={report.threats}
            onCitation={onCitation}
            title="Threats"
          />
          <ClaimSection
            competitorNames={competitorNames}
            items={report.opportunities}
            onCitation={onCitation}
            title="Opportunities"
          />
          <section>
            <h3 className="text-lg font-semibold">Uncertainties</h3>
            <ul className="mt-4 space-y-4">
              {report.uncertainties.map((item) => (
                <li className="text-sm leading-6 text-zinc-600 dark:text-zinc-400" key={item.statement}>
                  {item.statement}
                  <CitationButtons ids={item.evidence_ids} onCitation={onCitation} />
                </li>
              ))}
              {!report.uncertainties.length ? <li className="text-sm text-zinc-500">None stated.</li> : null}
            </ul>
          </section>
          <section>
            <h3 className="text-lg font-semibold">Evidence gaps</h3>
            <ul className="mt-4 space-y-5">
              {report.evidence_gaps.map((item) => (
                <li key={item.topic}>
                  <h4 className="text-sm font-semibold">{item.topic}</h4>
                  <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                    {item.description}
                  </p>
                  <p className="mt-1 text-xs text-zinc-500">{item.why_it_matters}</p>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>

      <div className="mt-12 flex flex-wrap items-center gap-3 border-t border-black/10 pt-6 text-sm dark:border-white/10">
        <span className="text-zinc-600 dark:text-zinc-400">Was this report useful?</span>
        {(["helpful", "not_helpful"] as const).map((value) => (
          <button
            aria-pressed={feedback === value}
            className={`rounded-full px-4 py-2 font-medium ${
              feedback === value
                ? "bg-foreground text-background"
                : "border border-black/15 dark:border-white/20"
            }`}
            key={value}
            onClick={() => onFeedback(value)}
            type="button"
          >
            {value === "helpful" ? "Helpful" : "Not helpful"}
          </button>
        ))}
      </div>
    </article>
  );
}

function ClaimSection({
  title,
  items,
  competitorNames,
  onCitation,
}: {
  title: string;
  items: StrategicClaim[];
  competitorNames: Map<string, string>;
  onCitation: (id: string) => void;
}) {
  return (
    <section>
      <h3 className="text-2xl font-semibold tracking-tight">{title}</h3>
      <ol className="mt-4 divide-y divide-black/10 dark:divide-white/10">
        {items.map((item) => (
          <li className="py-5 first:pt-0" key={`${item.statement}:${item.evidence_ids.join(":")}`}>
            <p className="leading-7">{item.statement}</p>
            <ClaimMeta
              competitorIds={item.competitor_ids}
              competitorNames={competitorNames}
              confidence={item.confidence}
              dateFrom={item.date_from}
              dateTo={item.date_to}
            />
            <CitationButtons ids={item.evidence_ids} onCitation={onCitation} />
          </li>
        ))}
        {!items.length ? <li className="py-4 text-sm text-zinc-500">No validated items.</li> : null}
      </ol>
    </section>
  );
}

function ComparisonSection({
  items,
  competitorNames,
  onCitation,
}: {
  items: StrategicComparison[];
  competitorNames: Map<string, string>;
  onCitation: (id: string) => void;
}) {
  if (!items.length) return null;
  return (
    <section>
      <h3 className="text-2xl font-semibold tracking-tight">Competitor comparisons</h3>
      <div className="mt-5 space-y-7">
        {items.map((item) => (
          <div className="border-l-2 border-zinc-300 pl-5 dark:border-zinc-700" key={item.topic}>
            <h4 className="font-semibold">{item.topic}</h4>
            <p className="mt-2 leading-7 text-zinc-700 dark:text-zinc-300">{item.comparison}</p>
            <ClaimMeta
              competitorIds={item.competitor_ids}
              competitorNames={competitorNames}
              confidence={item.confidence}
              dateFrom={item.date_from}
              dateTo={item.date_to}
            />
            <CitationButtons ids={item.evidence_ids} onCitation={onCitation} />
          </div>
        ))}
      </div>
    </section>
  );
}

function RecommendationSection({
  items,
  competitorNames,
  onCitation,
}: {
  items: StrategicRecommendation[];
  competitorNames: Map<string, string>;
  onCitation: (id: string) => void;
}) {
  return (
    <section>
      <h3 className="text-2xl font-semibold tracking-tight">Advisory recommendations</h3>
      <div className="mt-5 grid gap-5">
        {items.map((item) => (
          <article className="rounded-xl border border-black/10 p-5 dark:border-white/15" key={item.title}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h4 className="text-lg font-semibold">{item.title}</h4>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge label={`Impact ${item.impact}`} />
                <Badge label={`Effort ${item.effort}`} />
                <Badge label={item.time_horizon.replaceAll("_", " ")} />
              </div>
            </div>
            <p className="mt-3 leading-7">{item.recommendation}</p>
            <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
              {item.rationale}
            </p>
            <ClaimMeta
              competitorIds={item.competitor_ids}
              competitorNames={competitorNames}
              confidence={item.confidence}
              dateFrom={item.date_from}
              dateTo={item.date_to}
            />
            <div className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
              <MiniList items={item.assumptions} title="Assumptions" />
              <MiniList items={item.risks} title="Risks" />
              <MiniList items={item.success_indicators} title="Success indicators" />
            </div>
            <CitationButtons ids={item.evidence_ids} onCitation={onCitation} />
          </article>
        ))}
        {!items.length ? (
          <p className="text-sm text-zinc-500">No validated strategic recommendations were generated.</p>
        ) : null}
      </div>
    </section>
  );
}

function ClaimMeta({
  confidence,
  competitorIds,
  competitorNames,
  dateFrom,
  dateTo,
}: {
  confidence: string;
  competitorIds: string[];
  competitorNames: Map<string, string>;
  dateFrom: string | null;
  dateTo: string | null;
}) {
  return (
    <p className="mt-2 text-xs text-zinc-500">
      {confidence} confidence
      {competitorIds.length
        ? ` · ${competitorIds.map((id) => competitorNames.get(id) ?? "Competitor").join(", ")}`
        : ""}
      {dateFrom ? ` · ${new Date(dateFrom).toLocaleDateString()}` : ""}
      {dateTo && dateTo !== dateFrom ? `–${new Date(dateTo).toLocaleDateString()}` : ""}
    </p>
  );
}

function CitationButtons({
  ids,
  onCitation,
}: {
  ids: string[];
  onCitation: (id: string) => void;
}) {
  if (!ids.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2" aria-label="Supporting evidence">
      {ids.map((id, index) => (
        <button
          className="rounded-md bg-zinc-100 px-2 py-1 text-xs font-semibold text-zinc-700 transition hover:bg-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
          key={id}
          onClick={() => onCitation(id)}
          title={`Open evidence ${id}`}
          type="button"
        >
          E{index + 1}
        </button>
      ))}
    </div>
  );
}

function Badge({ label }: { label: string }) {
  return <span className="rounded-full bg-zinc-100 px-2.5 py-1 capitalize text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">{label}</span>;
}

function MiniList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h5 className="font-semibold">{title}</h5>
      <ul className="mt-2 space-y-1 text-zinc-600 dark:text-zinc-400">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function ReportHistory({
  reports,
  runs,
  competitorNames,
  onOpen,
  onRerun,
  onRetry,
  disabled,
}: {
  reports: StrategicReport[];
  runs: StrategyRun[];
  competitorNames: Map<string, string>;
  onOpen: (id: string) => void;
  onRerun: (report: StrategicReport) => void;
  onRetry: (runId: string) => void;
  disabled: boolean;
}) {
  const failedRuns = runs.filter((item) => item.status === "failed");
  return (
    <section className="border-t border-black/10 py-10 dark:border-white/10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-2xl font-semibold tracking-tight">Report history</h2>
        <span className="text-sm text-zinc-500">{reports.length} completed reports</span>
      </div>
      <div className="mt-5 divide-y divide-black/10 dark:divide-white/10">
        {reports.map((item) => (
          <article className="grid gap-3 py-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center" key={item.id}>
            <div>
              <h3 className="font-semibold">{item.title}</h3>
              <p className="mt-1 text-xs text-zinc-500">
                {strategyModeLabel(item.mode)} · {new Date(item.created_at).toLocaleDateString()} ·{" "}
                {item.competitor_ids.map((id) => competitorNames.get(id) ?? "Competitor").join(", ")}
              </p>
              <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
                {item.evidence_coverage.cited_evidence_count} citations
                {item.deterministic_fallback ? " · Verified summary" : ""}
              </p>
            </div>
            <div className="flex flex-wrap gap-2 justify-self-start">
              <button
                className="rounded-full border border-black/15 px-4 py-2 text-sm font-semibold transition hover:border-black/40 dark:border-white/20 dark:hover:border-white/45"
                onClick={() => onOpen(item.id)}
                type="button"
              >
                Open report
              </button>
              <button
                className="rounded-full border border-black/15 px-4 py-2 text-sm font-semibold transition hover:border-black/40 disabled:opacity-45 dark:border-white/20 dark:hover:border-white/45"
                disabled={disabled}
                onClick={() => onRerun(item)}
                type="button"
              >
                Rerun with latest evidence
              </button>
            </div>
          </article>
        ))}
        {!reports.length ? (
          <p className="py-6 text-sm text-zinc-500">
            No completed reports in this company workspace yet.
          </p>
        ) : null}
      </div>
      {failedRuns.length ? (
        <div className="mt-7 border-t border-black/10 pt-5 dark:border-white/10">
          <h3 className="text-sm font-semibold">Runs requiring attention</h3>
          <ul className="mt-3 space-y-3">
            {failedRuns.slice(0, 5).map((item) => (
              <li className="flex flex-wrap items-center justify-between gap-3 text-sm" key={item.id}>
                <span className="text-zinc-600 dark:text-zinc-400">
                  {item.error_message ?? "Strategic analysis failed."}
                </span>
                <button
                  className="rounded-full border border-black/15 px-3 py-1.5 font-semibold disabled:opacity-45 dark:border-white/20"
                  disabled={disabled}
                  onClick={() => onRetry(item.id)}
                  type="button"
                >
                  Retry run
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {runs.some((item) => item.status === "cancelled") ? (
        <p className="mt-5 text-xs text-zinc-500">
          Cancelled runs remain auditable in run history without creating reports.
        </p>
      ) : null}
    </section>
  );
}

function CitationDrawer({
  citation,
  loading,
  onClose,
}: {
  citation: EvidenceExcerpt | null;
  loading: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    function close(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/25" role="presentation" onMouseDown={onClose}>
      <aside
        aria-label="Supporting evidence"
        aria-modal="true"
        className="h-full w-full max-w-lg overflow-y-auto bg-background p-6 shadow-2xl sm:p-8"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold">Supporting evidence</h2>
          <button aria-label="Close evidence" className="text-2xl leading-none" onClick={onClose} type="button">
            ×
          </button>
        </div>
        {loading ? (
          <p className="mt-8 text-sm text-zinc-500">Loading canonical evidence…</p>
        ) : citation ? (
          <div className="mt-8">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-zinc-500">
              {citation.competitor_name ?? "Company source"} ·{" "}
              {citation.source_type.replaceAll("_", " ")}
            </p>
            <h3 className="mt-3 text-xl font-semibold">{citation.title}</h3>
            {citation.section_title ? (
              <p className="mt-2 text-sm font-medium text-zinc-600 dark:text-zinc-400">
                {citation.section_title}
              </p>
            ) : null}
            <p className="mt-5 whitespace-pre-wrap text-sm leading-7 text-zinc-700 dark:text-zinc-300">
              {citation.content_excerpt}
            </p>
            {citation.before_excerpt || citation.after_excerpt ? (
              <div className="mt-6 grid gap-4 text-sm">
                {citation.before_excerpt ? <blockquote className="border-l-2 border-red-300 pl-4">{citation.before_excerpt}</blockquote> : null}
                {citation.after_excerpt ? <blockquote className="border-l-2 border-emerald-400 pl-4">{citation.after_excerpt}</blockquote> : null}
              </div>
            ) : null}
            <p className="mt-5 text-xs text-zinc-500">
              Observed {new Date(citation.observed_at).toLocaleString()}
              {citation.page_start ? ` · Page ${citation.page_start}${citation.page_end && citation.page_end !== citation.page_start ? `–${citation.page_end}` : ""}` : ""}
            </p>
            {citation.source_url ? (
              <a
                className="mt-6 inline-block text-sm font-semibold underline decoration-zinc-400 underline-offset-4"
                href={citation.source_url}
                rel="noreferrer"
                target="_blank"
              >
                Open public source
              </a>
            ) : null}
          </div>
        ) : null}
      </aside>
    </div>
  );
}

function upsertRun(items: StrategyRun[], run: StrategyRun): StrategyRun[] {
  return [run, ...items.filter((item) => item.id !== run.id)].slice(0, 25);
}

function isCancelled(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "cancelled";
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
