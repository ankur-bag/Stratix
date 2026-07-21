"use client";

import { useAuth } from "@clerk/nextjs";
import { useMemo } from "react";
import { ApiClient } from "@/lib/api/client";

export function useAuthenticatedApi(): ApiClient {
  const { getToken } = useAuth();
  return useMemo(() => new ApiClient(() => getToken()), [getToken]);
}
