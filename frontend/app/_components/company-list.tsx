"use client";

import { useAuth, useClerk, useOrganization, useOrganizationList } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { activateOrganization } from "@/lib/auth/organization";

export function CompanyList() {
  const router = useRouter();
  const clerk = useClerk();
  const { getToken } = useAuth();
  const { organization: activeOrganization } = useOrganization();
  const { isLoaded, setActive, userMemberships } = useOrganizationList({
    userMemberships: { infinite: true, pageSize: 20 },
  });
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function openCompany(organizationId: string) {
    if (openingId) return;
    setOpeningId(organizationId);
    setError(null);
    try {
      await activateOrganization({
        organizationId,
        setActive,
        getActiveOrganizationId: () => clerk.organization?.id ?? null,
      });
      const token = await getToken({ organizationId, skipCache: true });
      if (!token) throw new Error("A company-scoped session could not be created.");
      router.push("/route-after-auth");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "The company could not be opened.");
      setOpeningId(null);
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-sm font-medium text-zinc-500">Company workspaces</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Your companies</h1>
        </div>
        <Link className="rounded-full bg-foreground px-5 py-2.5 text-sm font-medium text-background" href="/companies/new">
          + Add company
        </Link>
      </div>
      {error ? <p className="mt-6 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300" role="alert">{error}</p> : null}
      {!isLoaded || userMemberships.isLoading ? (
        <p className="mt-10 text-sm text-zinc-500">Loading companies…</p>
      ) : userMemberships.data?.length ? (
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {userMemberships.data.map((membership) => {
            const organization = membership.organization;
            const isActive = organization.id === activeOrganization?.id;
            return (
              <article className="rounded-2xl border border-black/10 p-6 dark:border-white/10" key={membership.id}>
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold">{organization.name}</h2>
                    <p className="mt-1 text-sm capitalize text-zinc-500">{membership.role.replaceAll("org:", "").replaceAll("_", " ")}</p>
                  </div>
                  {isActive ? <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium dark:bg-zinc-800">Active</span> : null}
                </div>
                <button
                  className="mt-6 w-full rounded-full border border-black/15 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/15"
                  disabled={openingId !== null}
                  onClick={() => openCompany(organization.id)}
                  type="button"
                >
                  {openingId === organization.id ? "Opening…" : isActive ? "Open company" : "Switch and open"}
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="mt-10 rounded-2xl border border-dashed border-black/20 p-8 text-center text-sm text-zinc-500 dark:border-white/20">No company workspaces yet.</p>
      )}
      {userMemberships.hasNextPage ? (
        <button
          className="mt-6 rounded-full border border-black/15 px-5 py-2 text-sm font-medium dark:border-white/15"
          disabled={userMemberships.isFetching}
          onClick={() => userMemberships.fetchNext()}
          type="button"
        >
          {userMemberships.isFetching ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </>
  );
}
