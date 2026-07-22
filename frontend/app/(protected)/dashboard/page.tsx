import { ApprovedCompetitors } from "@/app/_components/approved-competitors";

export default function DashboardPage() {
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-12 sm:py-16">
      <p className="text-sm font-medium text-zinc-500">Workspace overview</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Your competitive set</h1>
      <p className="mt-3 max-w-2xl leading-7 text-zinc-600 dark:text-zinc-400">
        Track approved competitors through bounded public-source scans and objective, evidence-linked change signals.
      </p>
      <ApprovedCompetitors />
    </main>
  );
}
