import Link from "next/link";
import { ApprovedCompetitors } from "@/app/_components/approved-competitors";

export default function CompetitorsPage() {
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-12 sm:py-16">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-sm font-medium text-zinc-500">Competitors</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Approved companies</h1></div>
        <Link className="rounded-full border border-black/15 px-4 py-2 text-sm font-medium dark:border-white/15" href="/competitors/review">Review suggestions</Link>
      </div>
      <ApprovedCompetitors />
    </main>
  );
}
