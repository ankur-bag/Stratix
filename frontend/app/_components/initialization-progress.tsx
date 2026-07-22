"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type { DiscoveryJob, DiscoveryJobResponse, InitializationJob } from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";

const maximumPolls = 160;

export function InitializationProgress() {
  const api = useAuthenticatedApi();
  const router = useRouter();
  const searchParams = useSearchParams();
  const jobId = searchParams.get("jobId");
  const [job, setJob] = useState<InitializationJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  const continueToDiscovery = useCallback(async () => {
    let discovery: DiscoveryJob | null = null;
    try {
      discovery = await api.request<DiscoveryJob>("/api/v1/competitor-discovery/jobs/latest");
    } catch (lookupError) {
      if (!(lookupError instanceof ApiError) || lookupError.code !== "DISCOVERY_JOB_NOT_FOUND") {
        throw lookupError;
      }
    }
    if (!discovery) {
      try {
        const created = await api.request<DiscoveryJobResponse>(
          "/api/v1/competitor-discovery/jobs",
          {
            method: "POST",
            headers: { "Idempotency-Key": `discovery:${jobId}` },
          },
        );
        discovery = { ...created.job, candidate_count: 0, provider_error_count: 0, error_code: null, error_message: null };
      } catch (startError) {
        if (!(startError instanceof ApiError) || startError.code !== "DISCOVERY_ALREADY_RUNNING") {
          throw startError;
        }
        discovery = await api.request<DiscoveryJob>("/api/v1/competitor-discovery/jobs/latest");
      }
    }
    router.replace(`/competitors/review?jobId=${encodeURIComponent(discovery.id)}`);
  }, [api, jobId, router]);

  useEffect(() => {
    if (!jobId) return;
    const currentJobId = jobId;
    let cancelled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;

    async function poll() {
      try {
        const current = await api.request<InitializationJob>(
          `/api/v1/initialization-jobs/${encodeURIComponent(currentJobId)}`,
          { signal: controller.signal },
        );
        if (cancelled) return;
        setJob(current);
        if (current.status === "succeeded") {
          await continueToDiscovery();
          return;
        }
        if (current.status === "failed") return;
        polls += 1;
        if (polls >= maximumPolls) {
          setError("Initialization is still running. You can safely return and check again.");
          return;
        }
        timer = setTimeout(poll, 1500);
      } catch (pollError) {
        if (!cancelled && !(pollError instanceof ApiError && pollError.kind === "cancelled")) {
          setError(pollError instanceof ApiError ? pollError.message : "Initialization status could not be loaded.");
        }
      }
    }
    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, continueToDiscovery, jobId]);

  const progress = job?.progress_percent ?? 0;
  const displayedError = error ?? (!jobId ? "The initialization job identifier is missing." : null);
  return (
    <section className="w-full rounded-2xl border border-black/10 bg-white p-8 shadow-sm dark:border-white/10 dark:bg-zinc-950">
      <p className="text-sm font-medium text-zinc-500">Company intelligence bootstrap</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Preparing your workspace</h1>
      <p className="mt-3 text-zinc-600 dark:text-zinc-400">
        {job?.message ?? "Loading the latest initialization stage…"}
      </p>
      <div className="mt-8 h-2 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800" aria-label={`${progress}% complete`}>
        <div className="h-full rounded-full bg-zinc-900 transition-[width] duration-500 dark:bg-zinc-100" style={{ width: `${progress}%` }} />
      </div>
      <div className="mt-3 flex justify-between text-sm text-zinc-500">
        <span>{job?.stage?.replaceAll("_", " ") ?? "queued"}</span><span>{progress}%</span>
      </div>
      {job?.status === "failed" ? (
        <div className="mt-8 rounded-xl bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          <p>{job.error_message ?? "Initialization could not be completed."}</p>
          <button className="mt-3 font-medium underline" onClick={() => router.push("/onboarding")} type="button">Review company details</button>
        </div>
      ) : null}
      {displayedError ? <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">{displayedError}</p> : null}
    </section>
  );
}
