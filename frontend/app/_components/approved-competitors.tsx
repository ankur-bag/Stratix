"use client";

import { useEffect, useState } from "react";
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
    api.request<Competitor[]>("/api/v1/competitors")
      .then((competitors) => { if (!cancelled) setItems(competitors); })
      .catch((requestError: unknown) => { if (!cancelled) setError(requestError instanceof ApiError ? requestError.message : "Competitors could not be loaded."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api]);

  if (loading) return <p className="mt-10 text-sm text-zinc-500">Loading approved competitors…</p>;
  if (error) return <p role="alert" className="mt-10 text-sm text-red-600 dark:text-red-400">{error}</p>;
  if (!items.length) return <div className="mt-10 rounded-2xl border border-dashed border-black/20 p-10 text-center dark:border-white/20"><h2 className="font-semibold">No competitors approved yet</h2><p className="mt-2 text-sm text-zinc-500">Review discovery suggestions to create your competitive set.</p></div>;
  return (
    <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      {items.map((competitor) => (
        <article className="rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950" key={competitor.id}>
          <div className="flex items-start justify-between gap-3"><h2 className="text-lg font-semibold">{competitor.name}</h2><span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-200">Monitoring pending</span></div>
          <a className="mt-2 block text-sm text-zinc-500 hover:underline" href={competitor.canonical_url} rel="noreferrer" target="_blank">{competitor.canonical_domain}</a>
          <p className="mt-4 line-clamp-3 text-sm leading-6 text-zinc-600 dark:text-zinc-400">{competitor.description}</p>
          <p className="mt-5 text-xs font-medium uppercase tracking-wider text-zinc-500">Suggested relationship: {competitor.relationship_type}</p>
        </article>
      ))}
    </div>
  );
}
