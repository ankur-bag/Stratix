import { Suspense } from "react";
import { InitializationProgress } from "@/app/_components/initialization-progress";

export default function InitializationProgressPage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 items-center px-6 py-16">
      <Suspense fallback={<p className="text-zinc-500">Loading initialization status…</p>}>
        <InitializationProgress />
      </Suspense>
    </main>
  );
}
