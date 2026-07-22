"use client";

import { useAuth, useClerk, useOrganizationList } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import {
  activateOrganization,
  CompanyWorkspaceError,
  normalizeOptionalWebsite,
  validateCompanyName,
} from "@/lib/auth/organization";

export function NewCompanyForm() {
  const router = useRouter();
  const clerk = useClerk();
  const { getToken } = useAuth();
  const { createOrganization, isLoaded, setActive } = useOrganizationList();
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || !isLoaded) return;
    setSubmitting(true);
    setError(null);
    try {
      const companyName = validateCompanyName(name);
      const companyWebsite = normalizeOptionalWebsite(website);
      const created = await createOrganization({ name: companyName });
      await activateOrganization({
        organizationId: created.id,
        setActive,
        getActiveOrganizationId: () => clerk.organization?.id ?? null,
      });
      const token = await getToken({ organizationId: created.id, skipCache: true });
      if (!token) throw new Error("A company-scoped session could not be created.");
      const query = companyWebsite
        ? `?website=${encodeURIComponent(companyWebsite)}`
        : "";
      router.push(`/onboarding${query}`);
    } catch (createError) {
      setError(
        createError instanceof CompanyWorkspaceError
          ? createError.message
          : createError instanceof Error
            ? createError.message
            : "The company could not be created. Please try again.",
      );
      setSubmitting(false);
    }
  }

  return (
    <form className="mt-10 grid gap-6" onSubmit={handleSubmit}>
      <section className="grid gap-5 rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950">
        <label className="grid gap-2 text-sm font-medium">
          Company name
          <input
            autoComplete="organization"
            className="h-11 rounded-lg border border-black/15 bg-transparent px-3 font-normal outline-none transition focus:border-zinc-500 dark:border-white/15"
            maxLength={80}
            minLength={2}
            onChange={(event) => setName(event.target.value)}
            required
            value={name}
          />
        </label>
        <label className="grid gap-2 text-sm font-medium">
          Public website <span className="font-normal text-zinc-500">Optional</span>
          <input
            className="h-11 rounded-lg border border-black/15 bg-transparent px-3 font-normal outline-none transition focus:border-zinc-500 dark:border-white/15"
            onChange={(event) => setWebsite(event.target.value)}
            placeholder="https://example.com"
            type="url"
            value={website}
          />
        </label>
      </section>
      {error ? (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300" role="alert">
          {error}
        </p>
      ) : null}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Link className="text-center text-sm text-zinc-500 underline-offset-4 hover:underline" href="/companies">
          View my companies
        </Link>
        <button
          className="rounded-full bg-foreground px-6 py-3 font-medium text-background disabled:cursor-not-allowed disabled:opacity-50"
          disabled={submitting || !isLoaded}
          type="submit"
        >
          {submitting ? "Creating company…" : "Create company"}
        </button>
      </div>
    </form>
  );
}
