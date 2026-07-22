import { OnboardingForm } from "@/app/_components/onboarding-form";

export default function OnboardingPage() {
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-12 sm:py-16">
      <p className="text-sm font-medium text-zinc-500">Company setup</p>
      <h1 className="mt-2 max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
        Give Stratix the context to find your real competitive set.
      </h1>
      <p className="mt-4 max-w-2xl leading-7 text-zinc-600 dark:text-zinc-400">
        We use this information and your public website to build an evidence-backed company
        profile for the active company workspace.
      </p>
      <OnboardingForm />
    </main>
  );
}
