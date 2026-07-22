"use client";

import { useOrganization } from "@clerk/nextjs";

export function CompanyWorkspaceBoundary({ children }: { children: React.ReactNode }) {
  const { isLoaded, organization } = useOrganization();

  if (!isLoaded) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-16 text-sm text-zinc-500">
        Loading company workspace…
      </main>
    );
  }

  return <div className="flex flex-1 flex-col" key={organization?.id ?? "no-company"}>{children}</div>;
}
