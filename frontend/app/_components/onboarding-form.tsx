"use client";

import { useAuth, useOrganization, useOrganizationList } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { ApiClient, ApiError } from "@/lib/api/client";
import { ensureActiveOrganization } from "@/lib/auth/organization";
import type { CompanyOnboardingRequest, OnboardingResponse } from "@/lib/api/types";

type FormState = {
  companyName: string;
  websiteUrl: string;
  description: string;
  industry: string;
  businessStage: CompanyOnboardingRequest["business_stage"];
  productName: string;
  customerSegments: string;
  productCategories: string;
  useCases: string;
  differentiators: string;
  keywords: string;
  knownCompetitors: string;
  strategicGoals: string;
};

const initialState: FormState = {
  companyName: "",
  websiteUrl: "",
  description: "",
  industry: "",
  businessStage: "seed",
  productName: "",
  customerSegments: "",
  productCategories: "",
  useCases: "",
  differentiators: "",
  keywords: "",
  knownCompetitors: "",
  strategicGoals: "Track competitor product and positioning changes",
};

function list(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Company setup could not be submitted. Check the details and try again.";
}

export function OnboardingForm() {
  const router = useRouter();
  const { getToken } = useAuth();
  const { organization } = useOrganization();
  const { isLoaded, createOrganization, setActive } = useOrganizationList();
  const [form, setForm] = useState(initialState);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function update<Key extends keyof FormState>(key: Key, value: FormState[Key]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || !isLoaded) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const token = await ensureActiveOrganization({
        activeOrganizationId: organization?.id ?? null,
        companyName: form.companyName,
        createOrganization,
        setActive,
        getToken,
      });
      const api = new ApiClient(async () => token);
      const payload: CompanyOnboardingRequest = {
        company_name: form.companyName,
        website_url: form.websiteUrl,
        one_line_description: form.description,
        industry: form.industry,
        business_stage: form.businessStage,
        product_name: form.productName,
        target_customer_segments: list(form.customerSegments),
        product_categories: list(form.productCategories),
        primary_use_cases: list(form.useCases),
        differentiators: list(form.differentiators),
        keywords: list(form.keywords),
        known_competitors: list(form.knownCompetitors),
        strategic_goals: list(form.strategicGoals),
      };
      const response = await api.request<OnboardingResponse>("/api/v1/onboarding/company", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify(payload),
      });
      router.push(
        `/onboarding/progress?jobId=${encodeURIComponent(response.initialization_job.id)}`,
      );
    } catch (error) {
      setMessage(errorMessage(error));
      setSubmitting(false);
    }
  }

  return (
    <form className="mt-10 grid gap-6" onSubmit={handleSubmit}>
      <section className="grid gap-5 rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950 sm:grid-cols-2">
        <Field label="Company name" required value={form.companyName} onChange={(value) => update("companyName", value)} />
        <Field label="Public website" required type="url" placeholder="https://example.com" value={form.websiteUrl} onChange={(value) => update("websiteUrl", value)} />
        <Field className="sm:col-span-2" label="One-line description" required value={form.description} onChange={(value) => update("description", value)} />
        <Field label="Industry" required value={form.industry} onChange={(value) => update("industry", value)} />
        <label className="grid gap-2 text-sm font-medium">
          Business stage
          <select className="h-11 rounded-lg border border-black/15 bg-transparent px-3 font-normal dark:border-white/15" value={form.businessStage} onChange={(event) => update("businessStage", event.target.value as FormState["businessStage"])}>
            <option value="idea">Idea</option><option value="pre_seed">Pre-seed</option><option value="seed">Seed</option><option value="series_a">Series A</option><option value="growth">Growth</option><option value="established">Established</option>
          </select>
        </label>
        <Field label="Product name" required value={form.productName} onChange={(value) => update("productName", value)} />
        <Field label="Customer segments" required placeholder="Startups, product teams" value={form.customerSegments} onChange={(value) => update("customerSegments", value)} />
        <Field label="Product categories" placeholder="Market intelligence, analytics" value={form.productCategories} onChange={(value) => update("productCategories", value)} />
        <Field label="Primary use cases" required placeholder="Competitor tracking, market research" value={form.useCases} onChange={(value) => update("useCases", value)} />
        <Field label="Differentiators" placeholder="Evidence-backed insights" value={form.differentiators} onChange={(value) => update("differentiators", value)} />
        <Field label="Keywords" placeholder="competitive intelligence, strategy" value={form.keywords} onChange={(value) => update("keywords", value)} />
        <Field label="Known competitors" placeholder="Company A, Company B" value={form.knownCompetitors} onChange={(value) => update("knownCompetitors", value)} />
        <Field label="Strategic goals" required value={form.strategicGoals} onChange={(value) => update("strategicGoals", value)} />
      </section>
      {message ? <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{message}</p> : null}
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-zinc-500">Comma-separate multiple items.</p>
        <button className="rounded-full bg-foreground px-6 py-3 font-medium text-background disabled:cursor-not-allowed disabled:opacity-50" disabled={submitting || !isLoaded} type="submit">
          {submitting ? "Preparing workspace…" : "Build company profile"}
        </button>
      </div>
    </form>
  );
}

function Field({ label, value, onChange, className = "", type = "text", ...props }: { label: string; value: string; onChange: (value: string) => void; className?: string; type?: string; required?: boolean; placeholder?: string }) {
  return (
    <label className={`grid gap-2 text-sm font-medium ${className}`}>
      {label}
      <input {...props} className="h-11 rounded-lg border border-black/15 bg-transparent px-3 font-normal outline-none transition focus:border-zinc-500 dark:border-white/15" type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}
