"use client";

import { useOrganization } from "@clerk/nextjs";
import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type {
  Competitor,
  EvidenceBundle,
  EvidenceExcerpt,
  EvidenceSourceType,
  IntelligenceAnswerResponse,
  IntelligenceCategory,
  IntelligenceIndexingStatus,
  IntelligenceOverview,
} from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";
import {
  buildIntelligenceSearchInput,
  evidenceDate,
  intelligenceLabel,
  type IntelligenceFilters,
} from "@/lib/intelligence";

const categories: IntelligenceCategory[] = [
  "pricing",
  "features",
  "positioning",
  "integrations",
  "product",
  "content",
  "documentation",
  "customer_proof",
  "general",
];

const sourceTypes: EvidenceSourceType[] = [
  "competitor_website_snapshot",
  "competitor_signal",
  "competitor_pricing_signal",
  "competitor_feature_signal",
  "competitor_positioning_signal",
  "competitor_integration_signal",
  "competitor_content_signal",
];

type SearchMode = "evidence" | "answer";

export function IntelligenceWorkspace() {
  const { organization } = useOrganization();
  return (
    <OrganizationIntelligenceWorkspace
      key={organization?.id ?? "no-organization"}
    />
  );
}

function OrganizationIntelligenceWorkspace() {
  const api = useAuthenticatedApi();
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [overview, setOverview] = useState<IntelligenceOverview | null>(null);
  const [indexing, setIndexing] = useState<IntelligenceIndexingStatus | null>(null);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<IntelligenceFilters>({
    competitorId: "",
    category: "",
    sourceType: "",
    dateFrom: "",
    dateTo: "",
    includeCompanyContext: false,
  });
  const [mode, setMode] = useState<SearchMode>("evidence");
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [answer, setAnswer] = useState<IntelligenceAnswerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const activeSearch = useRef<AbortController | null>(null);
  const searchGeneration = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function load() {
      try {
        const [competitorData, overviewData, indexingData] = await Promise.all([
          api.listCompetitors(controller.signal),
          api.getIntelligenceOverview(controller.signal),
          api.getIntelligenceIndexingStatus(controller.signal),
        ]);
        if (!active) return;
        setCompetitors(competitorData);
        setOverview(overviewData);
        setIndexing(indexingData);
      } catch (loadError) {
        if (active && !isCancelled(loadError)) {
          setError(messageFor(loadError, "Intelligence workspace could not be loaded."));
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      activeSearch.current?.abort();
    };
  }, [api]);

  function resetSearchResults() {
    searchGeneration.current += 1;
    activeSearch.current?.abort();
    activeSearch.current = null;
    setSearching(false);
    setBundle(null);
    setAnswer(null);
  }

  function updateQuery(value: string) {
    resetSearchResults();
    setQuery(value);
  }

  function updateFilters(next: IntelligenceFilters) {
    resetSearchResults();
    setFilters(next);
  }

  function updateMode(next: SearchMode) {
    resetSearchResults();
    setMode(next);
  }

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = buildIntelligenceSearchInput(query, filters);
    if (input.query.length < 2 || searching) return;
    activeSearch.current?.abort();
    const controller = new AbortController();
    activeSearch.current = controller;
    const generation = ++searchGeneration.current;
    setSearching(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "answer") {
        const response = await api.answerIntelligence(input, controller.signal);
        if (generation !== searchGeneration.current) return;
        setAnswer(response);
        setBundle(response.bundle);
      } else {
        const response = await api.searchIntelligence(input, controller.signal);
        if (generation !== searchGeneration.current) return;
        setAnswer(null);
        setBundle(response.bundle);
      }
    } catch (searchError) {
      if (!isCancelled(searchError)) {
        setError(messageFor(searchError, "Intelligence search could not be completed."));
      }
    } finally {
      if (activeSearch.current === controller) {
        activeSearch.current = null;
        setSearching(false);
      }
    }
  }

  async function reindex() {
    if (reindexing) return;
    setReindexing(true);
    setError(null);
    setNotice(null);
    try {
      const response = await api.reindexIntelligence(
        filters.competitorId ? [filters.competitorId] : [],
      );
      setNotice(
        `Reindex job ${response.status}. Search remains available from the current active evidence.`,
      );
    } catch (reindexError) {
      setError(messageFor(reindexError, "Reindexing could not be queued."));
    } finally {
      setReindexing(false);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-14 sm:px-6">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Loading executive intelligence…
        </p>
      </main>
    );
  }

  const competitorNames = new Map(competitors.map((item) => [item.id, item.name]));
  const maxTrend = Math.max(1, ...(overview?.trends.map((item) => item.event_count) ?? [1]));

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-10 sm:px-6 sm:py-14">
      <header className="grid gap-8 border-b border-black/10 pb-9 dark:border-white/10 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <p className="text-sm font-medium text-zinc-600 dark:text-zinc-400">
            Executive intelligence
          </p>
          <h1 className="mt-2 max-w-3xl text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">
            Find the evidence behind market movement.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-zinc-600 dark:text-zinc-400">
            Search verified competitor history, compare cross-source changes, and
            keep every conclusion traceable to its source.
          </p>
        </div>
        <IndexingSummary status={indexing} onReindex={reindex} busy={reindexing} />
      </header>

      {(indexing?.warnings.length || overview?.warnings.length) ? (
        <div
          className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100"
          role="status"
        >
          <p>{indexing?.warnings[0] ?? overview?.warnings[0]}</p>
          <button
            className="font-semibold underline decoration-amber-500/60 underline-offset-4 disabled:opacity-50"
            disabled={reindexing}
            onClick={reindex}
            type="button"
          >
            {reindexing ? "Queuing…" : "Reindex evidence"}
          </button>
        </div>
      ) : null}

      <section className="mt-8 border-b border-black/10 pb-10 dark:border-white/10">
        <form onSubmit={submitSearch}>
          <div className="flex flex-col gap-3 sm:flex-row">
            <label className="sr-only" htmlFor="intelligence-query">
              Search competitor evidence
            </label>
            <input
              autoComplete="off"
              className="min-h-12 flex-1 rounded-xl border border-black/15 bg-transparent px-4 text-base outline-none transition placeholder:text-zinc-500 focus:border-black focus:ring-2 focus:ring-black/10 dark:border-white/20 dark:focus:border-white dark:focus:ring-white/10"
              id="intelligence-query"
              maxLength={2000}
              onChange={(event) => updateQuery(event.target.value)}
              placeholder="Ask what changed in pricing, positioning, or product…"
              value={query}
            />
            <button
              className="min-h-12 rounded-xl bg-foreground px-6 font-semibold text-background transition hover:opacity-85 disabled:opacity-45"
              disabled={query.trim().length < 2 || searching || !indexing?.indexing_available}
              type="submit"
            >
              {searching ? "Searching…" : mode === "answer" ? "Build answer" : "Search evidence"}
            </button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <FilterSelect
              label="Competitor"
              onChange={(value) =>
                updateFilters({ ...filters, competitorId: value })
              }
              options={competitors.map((item) => ({ label: item.name, value: item.id }))}
              value={filters.competitorId}
            />
            <FilterSelect
              label="Category"
              onChange={(value) =>
                updateFilters({
                  ...filters,
                  category: value as IntelligenceCategory | "",
                })
              }
              options={categories.map((item) => ({
                label: intelligenceLabel(item),
                value: item,
              }))}
              value={filters.category}
            />
            <FilterSelect
              label="Source"
              onChange={(value) =>
                updateFilters({
                  ...filters,
                  sourceType: value as EvidenceSourceType | "",
                })
              }
              options={sourceTypes.map((item) => ({
                label: intelligenceLabel(item.replace("competitor_", "")),
                value: item,
              }))}
              value={filters.sourceType}
            />
            <DateFilter
              label="From"
              onChange={(value) => updateFilters({ ...filters, dateFrom: value })}
              value={filters.dateFrom}
            />
            <DateFilter
              label="To"
              min={filters.dateFrom || undefined}
              onChange={(value) => updateFilters({ ...filters, dateTo: value })}
              value={filters.dateTo}
            />
            <label className="flex min-h-9 items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
              <input
                checked={filters.includeCompanyContext}
                className="size-4 rounded border-black/20 accent-zinc-950 dark:border-white/20 dark:accent-zinc-100"
                onChange={(event) =>
                  updateFilters({
                    ...filters,
                    includeCompanyContext: event.target.checked,
                  })
                }
                type="checkbox"
              />
              Include company context
            </label>
            <div
              aria-label="Search mode"
              className="ml-auto flex rounded-lg bg-zinc-100 p-1 dark:bg-zinc-900"
              role="group"
            >
              {(["evidence", "answer"] as const).map((item) => (
                <button
                  aria-pressed={mode === item}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                    mode === item
                      ? "bg-white text-zinc-950 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
                  }`}
                  key={item}
                  onClick={() => updateMode(item)}
                  type="button"
                >
                  {item === "evidence" ? "Evidence" : "Answer"}
                </button>
              ))}
            </div>
          </div>
        </form>
      </section>

      {error ? (
        <p
          className="mt-6 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-200"
          role="alert"
        >
          {error}
        </p>
      ) : null}
      {notice ? (
        <p
          className="mt-6 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100"
          role="status"
        >
          {notice}
        </p>
      ) : null}

      {bundle ? (
        <SearchResults
          answer={answer}
          bundle={bundle}
          competitorNames={competitorNames}
        />
      ) : (
        <Overview
          competitorNames={competitorNames}
          maxTrend={maxTrend}
          overview={overview}
        />
      )}
    </main>
  );
}

function IndexingSummary({
  status,
  onReindex,
  busy,
}: {
  status: IntelligenceIndexingStatus | null;
  onReindex: () => void;
  busy: boolean;
}) {
  const label = !status?.indexing_available
    ? "Semantic indexing unavailable"
    : status.failed_job_count > 0
      ? "Semantic search partially available"
      : status.pending_job_count > 0
        ? "Indexing recent evidence"
        : status.inactive_evidence_count > 0 || !status.active_version_consistent
          ? "Reindex required"
          : "Up to date";
  return (
    <div className="min-w-64 border-t border-black/10 pt-4 text-sm dark:border-white/10 lg:border-l lg:border-t-0 lg:pl-7 lg:pt-0">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={`size-2 rounded-full ${
            status?.indexing_available &&
            !status.failed_job_count &&
            !status.pending_job_count &&
            !status.inactive_evidence_count &&
            status.active_version_consistent
              ? "bg-emerald-500"
              : "bg-amber-500"
          }`}
        />
        <span className="font-medium">{label}</span>
      </div>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        {status?.active_evidence_count ?? 0} active evidence records
      </p>
      {status?.pending_job_count ? (
        <p className="mt-1 text-zinc-600 dark:text-zinc-400">
          {status.pending_job_count} indexing job
          {status.pending_job_count === 1 ? "" : "s"} in progress
        </p>
      ) : null}
      <button
        className="mt-3 text-sm font-semibold underline decoration-zinc-400 underline-offset-4 disabled:opacity-50"
        disabled={busy}
        onClick={onReindex}
        type="button"
      >
        {busy ? "Queuing…" : "Reindex workspace"}
      </button>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
      <span>{label}</span>
      <select
        className="min-h-9 rounded-lg border border-black/15 bg-background px-2 text-foreground outline-none focus:ring-2 focus:ring-black/10 dark:border-white/20 dark:focus:ring-white/10"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function DateFilter({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: string;
  min?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
      <span>{label}</span>
      <input
        className="min-h-9 rounded-lg border border-black/15 bg-background px-2 text-foreground outline-none focus:ring-2 focus:ring-black/10 dark:border-white/20 dark:focus:ring-white/10"
        min={min}
        onChange={(event) => onChange(event.target.value)}
        type="date"
        value={value}
      />
    </label>
  );
}

function SearchResults({
  answer,
  bundle,
  competitorNames,
}: {
  answer: IntelligenceAnswerResponse | null;
  bundle: EvidenceBundle;
  competitorNames: Map<string, string>;
}) {
  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-2xl font-semibold tracking-tight">
          {answer ? "Evidence-backed answer" : "Ranked evidence"}
        </h2>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {bundle.items.length} excerpts · {bundle.coverage.competitor_count} competitors ·{" "}
          {bundle.coverage.source_type_count} source types
        </p>
      </div>
      {answer ? (
        <div className="mt-6 max-w-4xl border-y border-black/10 py-6 dark:border-white/10">
          <p className="text-lg leading-8">{answer.answer}</p>
          {answer.inferences.length ? (
            <div className="mt-5">
              <h3 className="text-sm font-semibold">Inference</h3>
              <ul className="mt-2 space-y-1 text-sm text-zinc-600 dark:text-zinc-400">
                {answer.inferences.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      {bundle.warnings.map((warning) => (
        <p className="mt-6 text-sm text-amber-800 dark:text-amber-300" key={warning}>
          {warning}
        </p>
      ))}
      <ol className="mt-3 divide-y divide-black/10 dark:divide-white/10">
        {bundle.items.map((item, index) => (
          <EvidenceRow
            competitorName={
              item.competitor_id
                ? competitorNames.get(item.competitor_id) ?? "Competitor"
                : "Company context"
            }
            index={index}
            item={item}
            key={item.evidence_id}
          />
        ))}
      </ol>
    </section>
  );
}

function EvidenceRow({
  item,
  index,
  competitorName,
}: {
  item: EvidenceExcerpt;
  index: number;
  competitorName: string;
}) {
  return (
    <li className="grid gap-3 py-6 md:grid-cols-[3rem_minmax(0,1fr)_auto]">
      <span className="text-sm font-semibold text-zinc-500">E{index + 1}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 className="font-semibold">{item.title}</h3>
          <span className="text-xs text-zinc-600 dark:text-zinc-400">
            {competitorName} · {intelligenceLabel(item.signal_category)}
          </span>
        </div>
        {item.section_title ? (
          <p className="mt-1 text-xs font-medium text-zinc-500">{item.section_title}</p>
        ) : null}
        <p className="mt-2 max-w-4xl text-sm leading-6 text-zinc-700 dark:text-zinc-300">
          {item.content_excerpt}
        </p>
        <p className="mt-2 text-xs text-zinc-500">
          {intelligenceLabel(item.source_type.replace("competitor_", ""))} ·{" "}
          {evidenceDate(item.observed_at)}
          {item.page_start
            ? ` · Page ${item.page_start}${item.page_end && item.page_end !== item.page_start ? `–${item.page_end}` : ""}`
            : ""}
        </p>
      </div>
      {item.source_url ? (
        <a
          className="self-start text-sm font-semibold underline decoration-zinc-400 underline-offset-4"
          href={item.source_url}
          rel="noreferrer"
          target="_blank"
        >
          Source
          <span className="sr-only"> for evidence E{index + 1}</span>
        </a>
      ) : null}
    </li>
  );
}

function Overview({
  overview,
  competitorNames,
  maxTrend,
}: {
  overview: IntelligenceOverview | null;
  competitorNames: Map<string, string>;
  maxTrend: number;
}) {
  return (
    <div className="mt-10 grid gap-12 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
      <section>
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-2xl font-semibold tracking-tight">Recent changes</h2>
          <span className="text-sm text-zinc-500">Last 180 days</span>
        </div>
        {overview?.recent_changes.length ? (
          <ol className="mt-4 divide-y divide-black/10 dark:divide-white/10">
            {overview.recent_changes.slice(0, 12).map((item) => (
              <li className="py-5" key={item.evidence_id}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-semibold">{item.title}</h3>
                  <time className="text-xs text-zinc-500" dateTime={item.observed_at}>
                    {evidenceDate(item.observed_at)}
                  </time>
                </div>
                <p className="mt-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
                  {intelligenceLabel(item.category)}
                </p>
                <p className="mt-2 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
                  {item.excerpt}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-6 text-sm text-zinc-600 dark:text-zinc-400">
            No indexed changes yet. Complete a competitor scan, then reindex the workspace.
          </p>
        )}
      </section>

      <aside className="space-y-10">
        <section>
          <h2 className="text-lg font-semibold">Activity by category</h2>
          <div className="mt-5 space-y-4">
            {overview?.trends.length ? (
              overview.trends.map((trend) => (
                <div key={trend.category}>
                  <div className="flex justify-between gap-3 text-sm">
                    <span>{intelligenceLabel(trend.category)}</span>
                    <span className="tabular-nums text-zinc-500">{trend.event_count}</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-900">
                    <div
                      className="h-full rounded-full bg-zinc-950 transition-[width] duration-500 ease-out dark:bg-zinc-100"
                      style={{ width: `${Math.max(5, (trend.event_count / maxTrend) * 100)}%` }}
                    />
                  </div>
                </div>
              ))
            ) : (
              <p className="text-sm text-zinc-500">Trend data will appear after indexing.</p>
            )}
          </div>
        </section>

        <section>
          <h2 className="text-lg font-semibold">Cross-source patterns</h2>
          <ul className="mt-4 space-y-5">
            {overview?.correlations.length ? (
              overview.correlations.slice(0, 6).map((item) => (
                <li key={`${item.type}:${item.evidence_ids.join(":")}`}>
                  <h3 className="text-sm font-semibold">{item.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-zinc-600 dark:text-zinc-400">
                    {item.summary}
                  </p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {item.competitor_ids
                      .map((id) => competitorNames.get(id) ?? "Competitor")
                      .join(", ")}
                  </p>
                </li>
              ))
            ) : (
              <li className="text-sm text-zinc-500">
                Patterns emerge when multiple evidence sources overlap.
              </li>
            )}
          </ul>
        </section>
      </aside>
    </div>
  );
}

function isCancelled(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "cancelled";
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
