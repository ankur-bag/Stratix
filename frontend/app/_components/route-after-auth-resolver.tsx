"use client";

import { useAuth, useClerk, useOrganization } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiClient, ApiError } from "@/lib/api/client";
import type { OnboardingStatus } from "@/lib/api/types";
import { routeForOnboardingStatus } from "@/lib/auth/workspace-routing";

export function RouteAfterAuthResolver() {
  const router = useRouter();
  const clerk = useClerk();
  const { getToken, isLoaded: isAuthLoaded, isSignedIn } = useAuth();
  const { isLoaded: isOrganizationLoaded, organization } = useOrganization();
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const organizationId = organization?.id ?? null;

  useEffect(() => {
    if (!isAuthLoaded || !isOrganizationLoaded) return;
    if (!isSignedIn) {
      router.replace("/sign-in");
      return;
    }
    if (!organizationId) {
      router.replace("/companies/new");
      return;
    }

    const controller = new AbortController();
    const api = new ApiClient(
      getToken,
      undefined,
      organizationId,
      (requestedId) => clerk.organization?.id === requestedId,
    );

    void api
      .request<OnboardingStatus>("/api/v1/onboarding/status", {
        signal: controller.signal,
      })
      .then((status) => router.replace(routeForOnboardingStatus(status)))
      .catch((requestError: unknown) => {
        if (requestError instanceof ApiError && requestError.kind === "cancelled") return;
        if (requestError instanceof ApiError && requestError.kind === "organization_switch") {
          router.refresh();
          return;
        }
        setError(
          requestError instanceof ApiError
            ? requestError.message
            : "This company workspace could not be opened.",
        );
      });

    return () => controller.abort();
  }, [attempt, clerk, getToken, isAuthLoaded, isOrganizationLoaded, isSignedIn, organizationId, router]);

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 items-center px-6 py-20">
      <section className="w-full rounded-2xl border border-black/10 p-8 text-center dark:border-white/10">
        <p className="text-sm font-medium text-zinc-500">Company workspace</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {error ? "We could not open this company" : "Opening your company…"}
        </h1>
        <p className="mt-3 text-sm leading-6 text-zinc-500">
          {error ?? "Checking this company’s setup and sending you to the right place."}
        </p>
        {error ? (
          <button
            className="mt-6 rounded-full bg-foreground px-5 py-2.5 text-sm font-medium text-background"
            onClick={() => { setError(null); setAttempt((value) => value + 1); }}
            type="button"
          >
            Try again
          </button>
        ) : null}
      </section>
    </main>
  );
}
