"use client";

import { useOrganization } from "@clerk/nextjs";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type {
  CompanyOnboardingRequest,
  OnboardingResponse,
  OnboardingStatus,
} from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";
import { routeForOnboardingStatus } from "@/lib/auth/workspace-routing";

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

const businessStages: Array<{ value: FormState["businessStage"]; label: string }> = [
  { value: "idea", label: "Idea" },
  { value: "pre_seed", label: "Pre-seed" },
  { value: "seed", label: "Seed" },
  { value: "series_a", label: "Series A" },
  { value: "growth", label: "Growth" },
  { value: "established", label: "Established" },
];

function list(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Company setup could not be submitted. Check the details and try again.";
}

export function OnboardingForm() {
  const { isLoaded, organization } = useOrganization();
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (isLoaded && !organization) router.replace("/companies/new");
  }, [isLoaded, organization, router]);

  if (!isLoaded || !organization) {
    return <p className="mt-10 text-sm text-zinc-500">Loading company workspace…</p>;
  }

  return (
    <ActiveCompanyOnboardingForm
      companyName={organization.name}
      key={organization.id}
      websiteUrl={searchParams.get("website") ?? ""}
    />
  );
}

function ActiveCompanyOnboardingForm({ companyName, websiteUrl }: { companyName: string; websiteUrl: string }) {
  const router = useRouter();
  const api = useAuthenticatedApi();
  const [form, setForm] = useState({ ...initialState, companyName, websiteUrl });
  const [checking, setChecking] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void api
      .request<OnboardingStatus>("/api/v1/onboarding/status", { signal: controller.signal })
      .then((status) => {
        if (status.has_company_profile) {
          router.replace(routeForOnboardingStatus(status));
          return;
        }
        setChecking(false);
      })
      .catch((statusError: unknown) => {
        if (statusError instanceof ApiError && statusError.kind === "cancelled") return;
        setMessage(errorMessage(statusError));
        setChecking(false);
      });
    return () => controller.abort();
  }, [api, router]);

  function update<Key extends keyof FormState>(key: Key, value: FormState[Key]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || checking) return;
    setSubmitting(true);
    setMessage(null);
    try {
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
        headers: { "Idempotency-Key": `onboarding:${crypto.randomUUID()}` },
        body: JSON.stringify(payload),
      });
      router.push(
        `/onboarding/progress?jobId=${encodeURIComponent(response.initialization_job.id)}`,
      );
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.kind === "cancelled" || error.kind === "organization_switch")
      ) return;
      setMessage(errorMessage(error));
      setSubmitting(false);
    }
  }

  return (
    <form aria-busy={checking} className="mt-10 grid gap-6" onSubmit={handleSubmit}>
      <section className="grid gap-5 rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950 sm:grid-cols-2">
        <Field label="Company name" required value={form.companyName} onChange={(value) => update("companyName", value)} />
        <Field label="Public website" required type="url" placeholder="https://example.com" value={form.websiteUrl} onChange={(value) => update("websiteUrl", value)} />
        <Field className="sm:col-span-2" label="One-line description" required value={form.description} onChange={(value) => update("description", value)} />
        <Field label="Industry" required value={form.industry} onChange={(value) => update("industry", value)} />
        <label className="grid min-w-0 gap-2 text-sm font-medium">
          Business stage
          <select className="h-11 rounded-lg border border-black/15 bg-white px-3 font-normal outline-none transition focus:border-zinc-500 dark:border-white/15 dark:bg-zinc-950 dark:[color-scheme:dark]" value={form.businessStage} onChange={(event) => update("businessStage", event.target.value as FormState["businessStage"])}>
            {businessStages.map((stage) => <option className="bg-white text-zinc-950 dark:bg-zinc-950 dark:text-zinc-100" key={stage.value} value={stage.value}>{stage.label}</option>)}
          </select>
        </label>
        <Field label="Product name" required value={form.productName} onChange={(value) => update("productName", value)} />
        <Field label="Customer segments" multiline required placeholder="Startups, product teams" value={form.customerSegments} onChange={(value) => update("customerSegments", value)} />
        <Field label="Product categories" multiline placeholder="Market intelligence, analytics" value={form.productCategories} onChange={(value) => update("productCategories", value)} />
        <Field label="Primary use cases" multiline required placeholder="Competitor tracking, market research" value={form.useCases} onChange={(value) => update("useCases", value)} />
        <Field label="Differentiators" multiline placeholder="Evidence-backed insights" value={form.differentiators} onChange={(value) => update("differentiators", value)} />
        <Field label="Keywords" multiline placeholder="competitive intelligence, strategy" value={form.keywords} onChange={(value) => update("keywords", value)} />
        <Field label="Known competitors" multiline placeholder="Company A, Company B" value={form.knownCompetitors} onChange={(value) => update("knownCompetitors", value)} />
        <Field label="Strategic goals" multiline required value={form.strategicGoals} onChange={(value) => update("strategicGoals", value)} />
      </section>
      {message ? <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{message}</p> : null}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-zinc-500">Comma-separate multiple items.</p>
        <button className="w-full rounded-full bg-foreground px-6 py-3 font-medium text-background disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto" disabled={submitting || checking} type="submit">
          {checking ? "Checking company…" : submitting ? "Preparing workspace…" : "Build company profile"}
        </button>
      </div>
    </form>
  );
}

function Field({ label, value, onChange, className = "", type = "text", multiline = false, ...props }: { label: string; value: string; onChange: (value: string) => void; className?: string; type?: string; multiline?: boolean; required?: boolean; placeholder?: string }) {
  return (
    <label className={`grid min-w-0 gap-2 text-sm font-medium ${className}`}>
      {label}
      {multiline ? (
        <textarea {...props} className="min-h-20 resize-y rounded-lg border border-black/15 bg-transparent px-3 py-2.5 font-normal leading-5 outline-none transition focus:border-zinc-500 dark:border-white/15" rows={2} value={value} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input {...props} className="h-11 min-w-0 rounded-lg border border-black/15 bg-transparent px-3 font-normal outline-none transition focus:border-zinc-500 dark:border-white/15" type={type} value={value} onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}
