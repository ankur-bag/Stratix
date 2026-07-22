"use client";

import { useAuth, useClerk, useOrganization } from "@clerk/nextjs";
import { useMemo } from "react";
import { ApiClient } from "@/lib/api/client";

export function useAuthenticatedApi(): ApiClient {
  const { getToken } = useAuth();
  const clerk = useClerk();
  const { organization } = useOrganization();
  const activeOrganizationId = organization?.id ?? null;

  return useMemo(
    () =>
      new ApiClient(
        getToken,
        undefined,
        activeOrganizationId,
        (requestedOrganizationId) =>
          clerk.organization?.id === requestedOrganizationId,
      ),
    [activeOrganizationId, clerk, getToken],
  );
}
