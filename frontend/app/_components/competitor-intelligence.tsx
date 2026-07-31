"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useOrganization } from "@clerk/nextjs";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import {
  durableJobMessage,
  monitoringCadences,
  monitoringScheduleUpdate,
  monitoringState,
  organizationStateKey,
} from "@/lib/api/durable";
import { isActiveScan, scanOutcome } from "@/lib/api/monitoring";
import type {
  Competitor,
  CompetitorScan,
  CompetitorSignal,
  CompetitorSource,
  CompetitorSourceType,
  CompetitorTimelineItem,
  MonitoringCadence,
  MonitoringSchedule,
} from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";

const sourceTypes: CompetitorSourceType[] = [
  "website",
  "pricing",
  "product",
  "features",
  "solutions",
  "documentation",
  "changelog",
  "blog",
  "customers",
  "integrations",
  "about",
];

export function CompetitorIntelligence({ competitorId }: { competitorId: string }) {
  const { organization } = useOrganization();
  return (
    <OrganizationCompetitorIntelligence
      competitorId={competitorId}
      key={organizationStateKey(organization?.id, competitorId)}
    />
  );
}

function OrganizationCompetitorIntelligence({
  competitorId,
}: {
  competitorId: string;
}) {
  const api = useAuthenticatedApi();
  const [competitor, setCompetitor] = useState<Competitor | null>(null);
  const [sources, setSources] = useState<CompetitorSource[]>([]);
  const [scans, setScans] = useState<CompetitorScan[]>([]);
  const [signals, setSignals] = useState<CompetitorSignal[]>([]);
  const [timeline, setTimeline] = useState<CompetitorTimelineItem[]>([]);
  const [timelineCursor, setTimelineCursor] = useState<string | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [schedule, setSchedule] = useState<MonitoringSchedule | null>(null);
  const [activeScan, setActiveScan] = useState<CompetitorScan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState<CompetitorSourceType>("website");
  const activeScanId = isActiveScan(activeScan) ? activeScan?.id ?? null : null;

  const loadWorkspace = useCallback(async (signal?: AbortSignal) => {
    const [competitorData, sourcePage, scanPage, signalPage, scheduleData, timelinePage] = await Promise.all([
      api.getCompetitor(competitorId, signal),
      api.listCompetitorSources(competitorId, signal),
      api.listCompetitorScans(competitorId, signal),
      api.listCompetitorSignals(competitorId, signal),
      api.getMonitoringSchedule(competitorId, signal),
      api.getCompetitorTimeline(competitorId, { limit: 10 }, signal),
    ]);
    setCompetitor(competitorData);
    setSources(sourcePage.items);
    setScans(scanPage.items);
    setSignals(signalPage.items);
    setSchedule(scheduleData);
    setTimeline(timelinePage.items);
    setTimelineCursor(timelinePage.next_cursor);
    setActiveScan(scanPage.items.find(isActiveScan) ?? null);
  }, [api, competitorId]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    async function load() {
      try {
        await loadWorkspace(controller.signal);
      } catch (requestError) {
        if (!cancelled && !(requestError instanceof ApiError && requestError.kind === "cancelled")) {
          setError(errorMessage(requestError, "Competitor intelligence could not be loaded."));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [loadWorkspace]);

  useEffect(() => {
    if (!activeScanId) return;
    const scanId = activeScanId;
    let cancelled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    async function poll() {
      try {
        const current = await api.getCompetitorScan(scanId, controller.signal);
        if (cancelled) return;
        setActiveScan(current);
        setScans((items) => [current, ...items.filter((item) => item.id !== current.id)]);
        if (!isActiveScan(current)) {
          await loadWorkspace(controller.signal);
          return;
        }
        polls += 1;
        if (polls < 120) timer = setTimeout(poll, 2000);
        else setError("The scan is still running. You can safely return later.");
      } catch (pollError) {
        if (!cancelled && !(pollError instanceof ApiError && pollError.kind === "cancelled")) setError(errorMessage(pollError, "Scan progress could not be refreshed."));
      }
    }
    timer = setTimeout(poll, 1000);
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [activeScanId, api, loadWorkspace]);

  async function startScan(rediscoverSources: boolean) {
    if (!competitor || busy || isActiveScan(activeScan)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await api.startCompetitorScan(competitorId, {
        scan_type: competitor.baseline_status === "ready" ? "rescan" : "baseline",
        rediscover_sources: rediscoverSources,
      });
      setActiveScan(response.scan);
      setScans((items) => [response.scan, ...items]);
      setNotice(durableJobMessage(response.job));
    } catch (scanError) {
      setError(errorMessage(scanError, "The competitor scan could not be started."));
    } finally {
      setBusy(false);
    }
  }

  async function updateSchedule(cadence: MonitoringCadence, isEnabled: boolean) {
    if (!schedule || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.updateMonitoringSchedule(competitorId, {
        cadence,
        is_enabled: isEnabled && cadence !== "manual",
        timezone: schedule.timezone,
      });
      setSchedule(updated);
      setNotice(
        updated.is_enabled
          ? `Automatic monitoring is ${monitoringCadences
              .find((item) => item.value === updated.cadence)
              ?.label.toLowerCase()}.`
          : "Automatic monitoring is paused. You can still run a scan manually.",
      );
    } catch (scheduleError) {
      setError(
        errorMessage(scheduleError, "The monitoring schedule could not be updated."),
      );
    } finally {
      setBusy(false);
    }
  }

  async function runMonitoringNow() {
    if (!competitor || busy || isActiveScan(activeScan)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await api.runMonitoringNow(competitorId);
      setActiveScan(response.scan);
      setScans((items) => [
        response.scan,
        ...items.filter((item) => item.id !== response.scan.id),
      ]);
      setNotice(durableJobMessage(response.job));
    } catch (scanError) {
      setError(errorMessage(scanError, "The competitor scan could not be queued."));
    } finally {
      setBusy(false);
    }
  }

  async function addSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sourceUrl.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCompetitorSource(competitorId, {
        url: sourceUrl.trim(),
        type: sourceType,
        priority: sourceType === "pricing" || sourceType === "product" ? "high" : "medium",
      });
      setSources((items) => [...items, created]);
      setSourceUrl("");
    } catch (sourceError) {
      setError(errorMessage(sourceError, "The source could not be added."));
    } finally {
      setBusy(false);
    }
  }

  async function disableSource(sourceId: string) {
    if (busy || isActiveScan(activeScan)) return;
    setBusy(true);
    setError(null);
    try {
      await api.disableCompetitorSource(competitorId, sourceId);
      setSources((items) =>
        items.map((item) =>
          item.id === sourceId ? { ...item, enabled: false, status: "disabled" } : item,
        ),
      );
    } catch (sourceError) {
      setError(errorMessage(sourceError, "The source could not be disabled."));
    } finally {
      setBusy(false);
    }
  }

  async function updateSignal(signalId: string, status: "reviewed" | "dismissed") {
    try {
      const updated = await api.updateSignalStatus(signalId, status);
      setSignals((items) => items.map((item) => (item.id === signalId ? updated : item)));
    } catch (signalError) {
      setError(errorMessage(signalError, "The signal review state could not be saved."));
    }
  }

  async function loadMoreTimeline() {
    if (!timelineCursor || timelineLoading) return;
    setTimelineLoading(true);
    setError(null);
    try {
      const page = await api.getCompetitorTimeline(
        competitorId,
        { cursor: timelineCursor, limit: 10 },
      );
      setTimeline((items) => [
        ...items,
        ...page.items.filter(
          (candidate) => !items.some((item) => item.evidence_id === candidate.evidence_id),
        ),
      ]);
      setTimelineCursor(page.next_cursor);
    } catch (timelineError) {
      setError(errorMessage(timelineError, "More timeline evidence could not be loaded."));
    } finally {
      setTimelineLoading(false);
    }
  }

  if (loading) {
    return <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-16 text-sm text-zinc-500">Loading competitor intelligence…</main>;
  }
  if (!competitor) {
    return <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-16"><p role="alert" className="text-red-600 dark:text-red-400">{error ?? "The competitor was not found."}</p></main>;
  }

  const latestScan = activeScan ?? scans[0] ?? null;
  const controlsDisabled = busy || isActiveScan(latestScan);
  const enabledSources = sources.filter((source) => source.enabled);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10 sm:py-14">
      <Link className="text-sm text-zinc-500 underline-offset-4 hover:underline" href="/competitors">← Approved competitors</Link>
      <header className="mt-7 grid gap-8 border-b border-black/10 pb-10 dark:border-white/10 lg:grid-cols-[1fr_auto] lg:items-end">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-medium capitalize text-zinc-500">{competitor.relationship_type} competitor</p>
            <StatusPill value={competitor.monitoring_status} />
          </div>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight">{competitor.name}</h1>
          <a className="mt-2 inline-block text-sm text-zinc-500 underline-offset-4 hover:underline" href={competitor.canonical_url} rel="noreferrer" target="_blank">{competitor.canonical_domain}</a>
          <p className="mt-5 max-w-2xl leading-7 text-zinc-600 dark:text-zinc-400">{competitor.description}</p>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          <button className="rounded-full border border-black/15 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/15" disabled={controlsDisabled} onClick={() => startScan(true)} type="button">Rediscover sources and scan</button>
          <button className="rounded-full bg-foreground px-5 py-2 text-sm font-medium text-background disabled:cursor-not-allowed disabled:opacity-45" disabled={controlsDisabled || enabledSources.length === 0} onClick={() => startScan(false)} type="button">{competitor.baseline_status === "ready" ? "Run scan again" : "Create baseline"}</button>
        </div>
      </header>

      {error ? <p role="alert" className="mt-6 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{error}</p> : null}
      {notice ? <p role="status" className="mt-6 rounded-xl bg-blue-50 px-4 py-3 text-sm text-blue-800 dark:bg-blue-950/40 dark:text-blue-200">{notice}</p> : null}

      <section aria-label="Monitoring summary" className="grid gap-px overflow-hidden rounded-2xl border border-black/10 bg-black/10 dark:border-white/10 dark:bg-white/10 sm:grid-cols-4">
        <Summary label="Baseline" value={readable(competitor.baseline_status)} />
        <Summary label="Enabled sources" value={String(enabledSources.length)} />
        <Summary label="Observed signals" value={String(competitor.signal_count)} />
        <Summary label="Last scan" value={formatDate(competitor.last_scan_at)} />
      </section>

      {schedule ? (
        <section
          aria-busy={busy}
          aria-labelledby="monitoring-schedule-title"
          className="mt-8 grid gap-6 rounded-2xl border border-black/10 p-6 dark:border-white/10 lg:grid-cols-[1fr_auto] lg:items-center"
        >
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold" id="monitoring-schedule-title">
                Monitoring schedule
              </h2>
              <StatusPill value={monitoringState(schedule)} />
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-500">
              Scans are placed on the durable queue. Closing this page will not
              interrupt a queued or running scan.
            </p>
            <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-3">
              <ScheduleMetric label="Next run" value={formatDate(schedule.next_run_at)} />
              <ScheduleMetric
                label="Last success"
                value={formatDate(schedule.last_success_at)}
              />
              <ScheduleMetric
                label="Last failure"
                value={formatDate(schedule.last_failure_at)}
              />
            </dl>
            {schedule.degraded_reason ? (
              <p className="mt-4 text-sm text-amber-700 dark:text-amber-300">
                Automatic monitoring needs attention: {readable(schedule.degraded_reason)}.
              </p>
            ) : null}
          </div>
          <div className="grid gap-3 sm:grid-cols-[180px_auto_auto] lg:grid-cols-1">
            <label className="grid gap-2 text-sm font-medium">
              Cadence
              <select
                className="h-11 rounded-lg border border-black/15 bg-white px-3 font-normal dark:border-white/15 dark:bg-zinc-950"
                disabled={busy}
                onChange={(event) => {
                  const cadence = event.target.value as MonitoringCadence;
                  const update = monitoringScheduleUpdate(cadence);
                  void updateSchedule(update.cadence, update.is_enabled);
                }}
                value={schedule.cadence}
              >
                {monitoringCadences.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="h-11 self-end rounded-full border border-black/15 px-5 text-sm font-medium disabled:opacity-45 dark:border-white/15"
              disabled={busy}
              onClick={() =>
                void updateSchedule(
                  schedule.cadence === "manual" ? "weekly" : schedule.cadence,
                  !schedule.is_enabled,
                )
              }
              type="button"
            >
              {schedule.is_enabled ? "Pause" : "Enable"}
            </button>
            <button
              className="h-11 self-end rounded-full bg-foreground px-5 text-sm font-medium text-background disabled:opacity-45"
              disabled={controlsDisabled || enabledSources.length === 0}
              onClick={() => void runMonitoringNow()}
              type="button"
            >
              Run now
            </button>
          </div>
        </section>
      ) : null}

      {latestScan ? <ScanProgress scan={latestScan} /> : null}

      <section className="mt-14" aria-labelledby="evidence-timeline-title">
        <div>
          <p className="text-sm font-medium text-zinc-500">Semantic history</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight" id="evidence-timeline-title">
            Evidence timeline
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500">
            Canonical snapshots and detected changes, ordered newest first.
          </p>
        </div>
        {timeline.length ? (
          <>
            <ol className="mt-7 divide-y divide-black/10 border-y border-black/10 dark:divide-white/10 dark:border-white/10">
              {timeline.map((item) => (
                <li className="grid gap-3 py-5 sm:grid-cols-[140px_1fr]" key={item.evidence_id}>
                  <time className="text-sm text-zinc-500" dateTime={item.observed_at}>
                    {formatDate(item.observed_at)}
                  </time>
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.14em] text-zinc-500">
                      {readable(item.category)} · {readable(item.source_type)}
                    </p>
                    <h3 className="mt-1 font-semibold">{item.title}</h3>
                    <p className="mt-2 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                      {item.excerpt}
                    </p>
                    {item.before_excerpt || item.after_excerpt ? (
                      <div className="mt-4 grid gap-4 md:grid-cols-2">
                        <Evidence
                          label="Before"
                          text={item.before_excerpt ?? "No prior excerpt was available."}
                        />
                        <Evidence
                          label="After"
                          text={item.after_excerpt ?? "No later excerpt was available."}
                        />
                      </div>
                    ) : null}
                    {item.source_url ? (
                      <a
                        className="mt-3 inline-block text-sm font-medium underline underline-offset-4"
                        href={item.source_url}
                        rel="noreferrer"
                        target="_blank"
                      >
                        View source ↗
                      </a>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
            {timelineCursor ? (
              <button
                className="mt-5 rounded-full border border-black/15 px-5 py-2 text-sm font-medium disabled:opacity-45 dark:border-white/15"
                disabled={timelineLoading}
                onClick={() => void loadMoreTimeline()}
                type="button"
              >
                {timelineLoading ? "Loading…" : "Load earlier evidence"}
              </button>
            ) : null}
          </>
        ) : (
          <EmptyState
            title="No indexed history yet"
            body="Run a scan and wait for the evidence indexing worker to complete."
          />
        )}
      </section>

      <section className="mt-14">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><p className="text-sm font-medium text-zinc-500">Collection scope</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">Public sources</h2></div>
          <p className="max-w-md text-sm leading-6 text-zinc-500">Only registered public pages are scanned. Disabled and robots-blocked pages stay out of collection.</p>
        </div>
        <form className="mt-6 grid gap-3 rounded-2xl bg-zinc-50 p-4 dark:bg-zinc-900 sm:grid-cols-[1fr_180px_auto]" onSubmit={addSource}>
          <label className="grid gap-2 text-sm font-medium">Source URL<input className="h-11 rounded-lg border border-black/15 bg-white px-3 font-normal outline-none focus:border-zinc-500 dark:border-white/15 dark:bg-zinc-950" onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://competitor.com/pricing" type="url" value={sourceUrl} /></label>
          <label className="grid gap-2 text-sm font-medium">Page type<select className="h-11 rounded-lg border border-black/15 bg-white px-3 font-normal capitalize dark:border-white/15 dark:bg-zinc-950" onChange={(event) => setSourceType(event.target.value as CompetitorSourceType)} value={sourceType}>{sourceTypes.map((type) => <option key={type} value={type}>{readable(type)}</option>)}</select></label>
          <button className="h-11 self-end rounded-full border border-black/15 px-5 text-sm font-medium disabled:opacity-45 dark:border-white/15" disabled={busy || !sourceUrl.trim()} type="submit">Add source</button>
        </form>
        {sources.length ? <div className="mt-5 divide-y divide-black/10 border-y border-black/10 dark:divide-white/10 dark:border-white/10">{sources.map((source) => <SourceRow disabled={controlsDisabled} key={source.id} onDisable={() => disableSource(source.id)} source={source} />)}</div> : <EmptyState title="No usable sources" body="Add a public page from the approved competitor domain, or rediscover sources before creating a baseline." />}
      </section>

      <section className="mt-16">
        <div><p className="text-sm font-medium text-zinc-500">Objective observations</p><h2 className="mt-2 text-2xl font-semibold tracking-tight">Recent signals</h2><p className="mt-3 max-w-2xl text-sm leading-6 text-zinc-500">Signals record verified before-and-after changes. They are evidence, not strategic conclusions.</p></div>
        {signals.length ? <div className="mt-7 grid gap-6">{signals.map((signal) => <SignalCard key={signal.id} onUpdate={(status) => updateSignal(signal.id, status)} signal={signal} />)}</div> : <EmptyState title="No meaningful changes detected" body={competitor.baseline_status === "ready" ? "The baseline is ready. New signals appear only after a later scan finds a meaningful change." : "Create a baseline first. The initial scan stores evidence and does not create change signals."} />}
      </section>
    </main>
  );
}

function ScanProgress({ scan }: { scan: CompetitorScan }) {
  return (
    <section aria-live="polite" className="mt-8 rounded-2xl border border-black/10 p-6 dark:border-white/10">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-semibold">{isActiveScan(scan) ? scan.message : scanOutcome(scan)}</p><p className="mt-1 text-sm capitalize text-zinc-500">{readable(scan.stage)}</p></div><p className="text-sm font-medium tabular-nums">{scan.progress_percent}%</p></div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><div className="h-full bg-zinc-900 transition-[width] duration-500 dark:bg-zinc-100" style={{ width: `${scan.progress_percent}%` }} /></div>
      <dl className="mt-5 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4"><Metric label="Sources collected" value={scan.successful_source_count} /><Metric label="Sources failed" value={scan.failed_source_count} /><Metric label="Snapshots" value={scan.snapshot_count} /><Metric label="Signals" value={scan.signal_count} /></dl>
    </section>
  );
}

function SourceRow({ source, disabled, onDisable }: { source: CompetitorSource; disabled: boolean; onDisable: () => void }) {
  const warning = source.status === "blocked" ? "This page disallows public crawling." : source.failure_count > 1 ? "This page has repeatedly failed collection." : null;
  return (
    <article className="grid gap-4 py-5 sm:grid-cols-[1fr_auto] sm:items-center">
      <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium capitalize">{readable(source.type)}</h3><StatusPill value={source.status} /><span className="text-xs capitalize text-zinc-500">{source.priority} priority</span></div><a className="mt-1 block max-w-2xl truncate text-sm text-zinc-500 underline-offset-4 hover:underline" href={source.canonical_url} rel="noreferrer" target="_blank">{source.canonical_url}</a>{warning ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">{warning}</p> : null}</div>
      {source.enabled ? <button className="text-sm font-medium text-zinc-500 underline-offset-4 hover:underline disabled:opacity-45" disabled={disabled} onClick={onDisable} type="button">Disable</button> : null}
    </article>
  );
}

function SignalCard({ signal, onUpdate }: { signal: CompetitorSignal; onUpdate: (status: "reviewed" | "dismissed") => void }) {
  return (
    <article className="rounded-2xl border border-black/10 p-6 dark:border-white/10">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-medium uppercase tracking-[0.16em] text-zinc-500">Observed change · {readable(signal.type)}</p><h3 className="mt-2 text-xl font-semibold">{signal.title}</h3><p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">{signal.summary}</p></div><StatusPill value={signal.status} /></div>
      <div className="mt-6 grid gap-5 md:grid-cols-2"><Evidence label="Before" text={signal.before_excerpt ?? "No prior excerpt was available."} /><Evidence label="After" text={signal.after_excerpt} /></div>
      {signal.numeric_changes.length ? <div className="mt-5 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-zinc-500"><tr><th className="pb-2 font-medium">Observed field</th><th className="pb-2 font-medium">Before</th><th className="pb-2 font-medium">After</th><th className="pb-2 font-medium">Change</th></tr></thead><tbody>{signal.numeric_changes.map((change, index) => <tr className="border-t border-black/10 dark:border-white/10" key={`${change.field}-${index}`}><td className="py-3 pr-5 font-medium">{change.field}</td><td className="py-3 pr-5">{change.before ?? "—"}</td><td className="py-3 pr-5">{change.after ?? "—"}</td><td className="py-3 capitalize">{readable(change.change_type)}</td></tr>)}</tbody></table></div> : null}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-black/10 pt-5 text-sm dark:border-white/10"><div className="flex flex-wrap gap-x-5 gap-y-2 text-zinc-500"><span>Detected on {formatDate(signal.observed_at)}</span><span>Importance {Math.round(signal.importance_score * 100)}%</span><span>Confidence {Math.round(signal.confidence * 100)}%</span><a className="font-medium text-foreground underline-offset-4 hover:underline" href={signal.source_url} rel="noreferrer" target="_blank">View evidence source ↗</a></div>{signal.status === "new" ? <div className="flex gap-2"><button className="rounded-full border border-black/15 px-4 py-2 font-medium dark:border-white/15" onClick={() => onUpdate("dismissed")} type="button">Dismiss</button><button className="rounded-full bg-foreground px-4 py-2 font-medium text-background" onClick={() => onUpdate("reviewed")} type="button">Mark reviewed</button></div> : null}</div>
    </article>
  );
}

function Evidence({ label, text }: { label: string; text: string }) { return <div><p className="text-xs font-medium uppercase tracking-[0.16em] text-zinc-500">{label}</p><blockquote className="mt-2 text-sm leading-6 text-zinc-700 dark:text-zinc-300">{text}</blockquote></div>; }
function Summary({ label, value }: { label: string; value: string }) { return <div className="bg-background p-5"><dt className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</dt><dd className="mt-2 text-sm font-semibold">{value}</dd></div>; }
function Metric({ label, value }: { label: string; value: number }) { return <div><dt className="text-zinc-500">{label}</dt><dd className="mt-1 font-semibold tabular-nums">{value}</dd></div>; }
function ScheduleMetric({ label, value }: { label: string; value: string }) { return <div><dt className="text-zinc-500">{label}</dt><dd className="mt-1 font-medium">{value}</dd></div>; }
function EmptyState({ title, body }: { title: string; body: string }) { return <div className="mt-7 rounded-2xl border border-dashed border-black/20 px-6 py-10 text-center dark:border-white/20"><h3 className="font-semibold">{title}</h3><p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-zinc-500">{body}</p></div>; }
function StatusPill({ value }: { value: string }) { return <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium capitalize text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">{readable(value)}</span>; }
function readable(value: string): string { return value.replaceAll("_", " "); }
function formatDate(value: string | null): string { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not yet"; }
function errorMessage(error: unknown, fallback: string): string { return error instanceof ApiError ? error.message : fallback; }
