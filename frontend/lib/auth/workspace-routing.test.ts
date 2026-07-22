import assert from "node:assert/strict";
import test from "node:test";
import type { OnboardingStatus } from "../api/types.ts";
import { routeForOnboardingStatus } from "./workspace-routing.ts";

function status(input: Partial<OnboardingStatus>): OnboardingStatus {
  return {
    has_company_profile: false,
    onboarding_status: null,
    company_profile_id: null,
    latest_job: null,
    ...input,
  };
}

test("routes a company with no profile to onboarding", () => {
  assert.equal(routeForOnboardingStatus(status({})), "/onboarding");
});

test("routes a ready company to its dashboard", () => {
  assert.equal(
    routeForOnboardingStatus(status({ has_company_profile: true, onboarding_status: "ready" })),
    "/dashboard",
  );
});

test("routes an active job to company-scoped progress", () => {
  assert.equal(
    routeForOnboardingStatus(status({
      has_company_profile: true,
      onboarding_status: "initializing",
      latest_job: {
        id: "job/one",
        status: "running",
        stage: "crawling_website",
        progress_percent: 40,
        message: "Working",
      },
    })),
    "/onboarding/progress?jobId=job%2Fone",
  );
});

test("routes a failed profile to recoverable onboarding", () => {
  assert.equal(
    routeForOnboardingStatus(status({ has_company_profile: true, onboarding_status: "failed" })),
    "/onboarding?recovery=1",
  );
});
