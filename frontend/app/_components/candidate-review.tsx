"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type { CandidatePage, CandidateReviewResponse, CompetitorCandidate, DiscoveryJob } from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";

export function CandidateReview() {
  const api = useAuthenticatedApi();
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedJobId = searchParams.get("jobId");
  const [job, setJob] = useState<DiscoveryJob | null>(null);
  const [candidates, setCandidates] = useState<CompetitorCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCandidates = useCallback(async (jobId: string) => {
    const page = await api.request<CandidatePage>(
      `/api/v1/competitor-candidates?discovery_job_id=${encodeURIComponent(jobId)}&limit=100`,
    );
    setCandidates(page.items);
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    async function poll() {
      try {
        const current = await api.request<DiscoveryJob>(
          requestedJobId
            ? `/api/v1/competitor-discovery/jobs/${encodeURIComponent(requestedJobId)}`
            : "/api/v1/competitor-discovery/jobs/latest",
        );
        if (cancelled) return;
        setJob(current);
        if (current.status === "succeeded" || current.status === "partially_succeeded") {
          await loadCandidates(current.id);
          return;
        }
        if (current.status === "failed") return;
        polls += 1;
        if (polls < 160) timer = setTimeout(poll, 1500);
        else setError("Discovery is still running. You can safely return later.");
      } catch (pollError) {
        if (!cancelled) setError(pollError instanceof ApiError ? pollError.message : "Discovery status could not be loaded.");
      }
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [api, loadCandidates, requestedJobId]);

  function toggle(candidateId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(candidateId)) next.delete(candidateId);
      else next.add(candidateId);
      return next;
    });
  }

  async function review(candidateIds: string[], decision: "approved" | "rejected") {
    if (!candidateIds.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.request<CandidateReviewResponse>(
        "/api/v1/competitor-candidates/review",
        {
          method: "POST",
          body: JSON.stringify({
            decisions: candidateIds.map((candidate_id) => ({ candidate_id, decision })),
          }),
        },
      );
      const updates = new Map(response.results.map((item) => [item.candidate.id, item.candidate]));
      setCandidates((current) => current.map((item) => updates.get(item.id) ?? item));
      setSelected(new Set());
      if (decision === "approved") router.push("/dashboard");
    } catch (reviewError) {
      setError(reviewError instanceof ApiError ? reviewError.message : "The review decision could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  const pending = candidates.filter((candidate) => candidate.status === "pending");
  const isRunning = !job || job.status === "queued" || job.status === "running";

  return (
    <>
      <p className="text-sm font-medium text-zinc-500">Competitor discovery</p>
      <div className="mt-2 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Review the evidence, then choose who to monitor.</h1>
          <p className="mt-3 max-w-3xl leading-7 text-zinc-600 dark:text-zinc-400">
            These are ranked suggestions from public search evidence. Relationship labels and confidence are assessments, not verified facts.
          </p>
        </div>
        {selected.size ? (
          <div className="flex shrink-0 gap-2">
            <button className="rounded-full border border-black/15 px-4 py-2 text-sm font-medium disabled:opacity-50 dark:border-white/15" disabled={busy} onClick={() => review([...selected], "rejected")} type="button">Reject selected</button>
            <button className="rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50" disabled={busy} onClick={() => review([...selected], "approved")} type="button">Approve selected</button>
          </div>
        ) : null}
      </div>

      {isRunning ? <DiscoveryProgress job={job} /> : null}
      {job?.status === "partially_succeeded" ? (
        <p className="mt-8 rounded-xl border border-amber-300/60 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">Discovery completed with partial search coverage. Valid candidates and their evidence were preserved.</p>
      ) : null}
      {job?.status === "failed" ? (
        <p className="mt-8 rounded-xl bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{job.error_message ?? "Discovery could not be completed."}</p>
      ) : null}
      {error ? <p role="alert" className="mt-8 text-sm text-red-600 dark:text-red-400">{error}</p> : null}

      {!isRunning && job?.status !== "failed" && candidates.length === 0 ? (
        <div className="mt-10 rounded-2xl border border-dashed border-black/20 p-10 text-center dark:border-white/20">
          <h2 className="font-semibold">No candidates found</h2>
          <p className="mt-2 text-sm text-zinc-500">Refine your company context and run discovery again.</p>
        </div>
      ) : null}

      <div className="mt-10 grid gap-5 lg:grid-cols-2">
        {candidates.map((candidate) => (
          <CandidateCard key={candidate.id} candidate={candidate} checked={selected.has(candidate.id)} busy={busy} onToggle={() => toggle(candidate.id)} onReview={(decision) => review([candidate.id], decision)} />
        ))}
      </div>
      {pending.length > 0 && !selected.size ? <p className="mt-6 text-sm text-zinc-500">Select one or more candidates for a batch decision, or review them individually.</p> : null}
    </>
  );
}

function DiscoveryProgress({ job }: { job: DiscoveryJob | null }) {
  const progress = job?.progress_percent ?? 0;
  return (
    <section className="mt-10 rounded-2xl border border-black/10 p-6 dark:border-white/10">
      <div className="flex justify-between gap-4"><p>{job?.message ?? "Starting competitor discovery…"}</p><span className="text-sm text-zinc-500">{progress}%</span></div>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><div className="h-full bg-zinc-900 transition-[width] dark:bg-zinc-100" style={{ width: `${progress}%` }} /></div>
    </section>
  );
}

function CandidateCard({ candidate, checked, busy, onToggle, onReview }: { candidate: CompetitorCandidate; checked: boolean; busy: boolean; onToggle: () => void; onReview: (decision: "approved" | "rejected") => void }) {
  const reviewed = candidate.status !== "pending";
  return (
    <article className="flex flex-col rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950">
      <div className="flex items-start justify-between gap-4">
        <div><p className="text-xs font-medium uppercase tracking-wider text-zinc-500">#{candidate.rank} · Suggested relationship: {candidate.relationship_type}</p><h2 className="mt-2 text-xl font-semibold">{candidate.name}</h2><a className="mt-1 block text-sm text-zinc-500 underline-offset-4 hover:underline" href={candidate.canonical_url} rel="noreferrer" target="_blank">{candidate.canonical_domain}</a></div>
        {!reviewed ? <input aria-label={`Select ${candidate.name}`} checked={checked} className="mt-1 size-5" onChange={onToggle} type="checkbox" /> : <span className="rounded-full bg-zinc-100 px-3 py-1 text-xs font-medium capitalize dark:bg-zinc-800">{candidate.status}</span>}
      </div>
      <p className="mt-5 text-sm leading-6 text-zinc-600 dark:text-zinc-400">{candidate.description}</p>
      <div className="mt-5 grid grid-cols-2 gap-3 rounded-xl bg-zinc-50 p-4 text-sm dark:bg-zinc-900">
        <div><p className="text-zinc-500">Similarity score</p><p className="mt-1 font-semibold">{Math.round(candidate.similarity_score * 100)}%</p></div>
        <div><p className="text-zinc-500">Confidence</p><p className="mt-1 font-semibold">{Math.round(candidate.confidence * 100)}%</p></div>
      </div>
      <div className="mt-5"><h3 className="text-sm font-semibold">Why Stratix found this company</h3><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-600 dark:text-zinc-400">{candidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>
      {(candidate.matching_categories.length || candidate.matching_use_cases.length) ? <div className="mt-5 flex flex-wrap gap-2">{[...candidate.matching_categories, ...candidate.matching_use_cases].map((match) => <span className="rounded-full border border-black/10 px-3 py-1 text-xs dark:border-white/10" key={match}>{match}</span>)}</div> : null}
      <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium">Evidence ({candidate.evidence.length})</summary><ul className="mt-3 space-y-3">{candidate.evidence.slice(0, 4).map((evidence) => <li key={`${evidence.query}-${evidence.result_url}`}><a className="font-medium underline-offset-4 hover:underline" href={evidence.result_url} rel="noreferrer" target="_blank">{evidence.title}</a><p className="mt-1 line-clamp-2 text-zinc-500">Query: {evidence.query}</p></li>)}</ul></details>
      {!reviewed ? <div className="mt-6 flex gap-2 border-t border-black/10 pt-5 dark:border-white/10"><button className="flex-1 rounded-full border border-black/15 px-4 py-2 text-sm font-medium disabled:opacity-50 dark:border-white/15" disabled={busy} onClick={() => onReview("rejected")} type="button">Reject</button><button className="flex-1 rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background disabled:opacity-50" disabled={busy} onClick={() => onReview("approved")} type="button">Approve</button></div> : null}
    </article>
  );
}
