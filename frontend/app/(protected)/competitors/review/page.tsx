import { Suspense } from "react";
import { CandidateReview } from "@/app/_components/candidate-review";

export default function CandidateReviewPage() {
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-12 sm:py-16">
      <Suspense fallback={<p className="text-zinc-500">Loading competitor discovery…</p>}>
        <CandidateReview />
      </Suspense>
    </main>
  );
}
