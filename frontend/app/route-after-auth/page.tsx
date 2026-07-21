import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { ApiError, createApiClient } from "@/lib/api/client";
import type { OnboardingStatus } from "@/lib/api/types";

export default async function RouteAfterAuthPage() {
  const authState = await auth();

  if (!authState.isAuthenticated) redirect("/sign-in");
  if (!authState.orgId) redirect("/onboarding");

  const api = createApiClient(() => authState.getToken());
  try {
    const status = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
    if (status.onboarding_status === "ready") redirect("/dashboard");
    if (status.latest_job) {
      redirect(`/onboarding/progress?jobId=${encodeURIComponent(status.latest_job.id)}`);
    }
  } catch (error) {
    if (
      error instanceof ApiError &&
      (error.kind === "organization_required" || error.code === "COMPANY_PROFILE_NOT_FOUND")
    ) {
      redirect("/onboarding");
    }
    throw error;
  }

  redirect("/onboarding");
}
