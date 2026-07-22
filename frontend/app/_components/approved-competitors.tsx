"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import type { Competitor } from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";

export function ApprovedCompetitors() {
  const api = useAuthenticatedApi();
  const [items, setItems] = useState<Competitor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    api.listCompetitors(controller.signal)
      .then((competitors) => { if (!cancelled) setItems(competitors); })
      .catch((requestError: unknown) => {
        if (!cancelled && !(requestError instanceof ApiError && requestError.kind === "cancelled")) {
          setError(requestError instanceof ApiError ? requestError.message : "Competitors could not be loaded.");
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [api]);

  if (loading) return <p className="mt-10 text-sm text-zinc-500">Loading approved competitors…</p>;
  if (error) return <p role="alert" className="mt-10 text-sm text-red-600 dark:text-red-400">{error}</p>;
  if (!items.length) return <div className="mt-10 rounded-2xl border border-dashed border-black/20 p-10 text-center dark:border-white/20"><h2 className="font-semibold">No competitors approved yet</h2><p className="mt-2 text-sm text-zinc-500">Review discovery suggestions to create your competitive set.</p></div>;
  return (
    <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {items.map((competitor) => (
        <article className="rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950" key={competitor.id}>
          <div className="flex items-start justify-between gap-3"><h2 className="text-lg font-semibold"><Link className="underline-offset-4 hover:underline" href={`/competitors/${competitor.id}`}>{competitor.name}</Link></h2><span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium capitalize text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">{competitor.monitoring_status.replaceAll("_", " ")}</span></div>
          <a className="mt-2 block text-sm text-zinc-500 hover:underline" href={competitor.canonical_url} rel="noreferrer" target="_blank">{competitor.canonical_domain}</a>
          <p className="mt-4 line-clamp-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">{competitor.description}</p>
          <p className="mt-5 text-xs font-medium uppercase tracking-wider text-zinc-500">Suggested relationship: {competitor.relationship_type}</p>
          <div className="mt-5 flex gap-4 border-t border-black/10 pt-4 text-xs text-zinc-500 dark:border-white/10"><span>{competitor.source_count} sources</span><span>{competitor.signal_count} signals</span><span className="capitalize">{competitor.baseline_status.replaceAll("_", " ")}</span></div>
        </article>
      ))}
    </div>
  );
}
