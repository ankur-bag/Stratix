import type { OnboardingStatus } from "@/lib/api/types";

export function routeForOnboardingStatus(status: OnboardingStatus): string {
  if (!status.has_company_profile) return "/onboarding";
  if (status.onboarding_status === "ready") return "/dashboard";
  if (status.latest_job?.id) {
    return `/onboarding/progress?jobId=${encodeURIComponent(status.latest_job.id)}`;
  }
  if (status.onboarding_status === "failed") return "/onboarding?recovery=1";
  return "/onboarding";
}
