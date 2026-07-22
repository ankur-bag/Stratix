import { NewCompanyForm } from "@/app/_components/new-company-form";

export default function NewCompanyPage() {
  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-6 py-12 sm:py-16">
      <p className="text-sm font-medium text-zinc-500">Company workspaces</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Add a company</h1>
      <p className="mt-4 max-w-xl leading-7 text-zinc-600 dark:text-zinc-400">
        Each company has its own profile, competitors, evidence, scans, and signals.
      </p>
      <NewCompanyForm />
    </main>
  );
}
