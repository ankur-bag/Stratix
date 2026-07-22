import { HomeAuthActions } from "@/app/_components/auth-actions";

export default function Home() {
  return (
    <main className="flex flex-1 items-center bg-zinc-50 px-6 py-20 dark:bg-zinc-950">
      <div className="mx-auto grid w-full max-w-6xl gap-12 lg:grid-cols-[1.2fr_0.8fr] lg:items-center">
        <section>
          <p className="text-sm font-medium text-zinc-500">Evidence-backed competitive intelligence</p>
          <h1 className="mt-4 max-w-3xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Know who is moving, what changed, and why it may matter.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-zinc-600 dark:text-zinc-400">
            Stratix turns public competitor evidence into a traceable intelligence workspace for startup and product teams.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <HomeAuthActions />
          </div>
        </section>
        <section className="rounded-3xl border border-black/10 bg-white p-7 shadow-sm dark:border-white/10 dark:bg-black">
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-zinc-500">Phase 3 workflow</p>
          <ol className="mt-6 space-y-5">
            {["Build observed company context", "Discover candidates from public evidence", "Review, approve, and retain the audit trail"].map((item, index) => (
              <li className="flex gap-4" key={item}><span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-zinc-100 text-sm font-semibold dark:bg-zinc-900">{index + 1}</span><p className="pt-1 text-sm leading-6 text-zinc-700 dark:text-zinc-300">{item}</p></li>
            ))}
          </ol>
        </section>
      </div>
    </main>
  );
}
