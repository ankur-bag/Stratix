"use client";

import {
  OrganizationSwitcher,
  SignInButton,
  SignUpButton,
  UserButton,
  useAuth,
  useOrganization,
} from "@clerk/nextjs";
import Link from "next/link";
import { NotificationCenter } from "@/app/_components/notification-center";
import { organizationStateKey } from "@/lib/api/durable";

export function HeaderAuthActions() {
  const { isLoaded, isSignedIn } = useAuth();
  const { organization } = useOrganization();

  if (!isLoaded || isSignedIn === undefined) {
    return <div aria-hidden="true" className="h-9 w-24" />;
  }

  if (isSignedIn) {
    return (
      <>
        <div className="hidden items-center gap-4 text-sm text-zinc-600 dark:text-zinc-400 md:flex">
          <Link className="transition hover:text-foreground" href="/dashboard">Dashboard</Link>
          <Link className="transition hover:text-foreground" href="/competitors">Competitors</Link>
          <Link className="transition hover:text-foreground" href="/intelligence">Intelligence</Link>
          <Link className="transition hover:text-foreground" href="/strategy">Strategy</Link>
          <Link className="transition hover:text-foreground" href="/settings">Settings</Link>
        </div>
        {organization ? (
          <NotificationCenter
            key={organizationStateKey(organization.id, "notifications")}
          />
        ) : null}
        <OrganizationSwitcher
          afterSelectOrganizationUrl="/route-after-auth"
          createOrganizationMode="navigation"
          createOrganizationUrl="/companies/new"
          hidePersonal
        />
        <Link
          className="whitespace-nowrap rounded-full border border-black/15 px-3 py-2 text-sm font-medium transition hover:border-black/30 dark:border-white/15 dark:hover:border-white/30"
          href="/companies/new"
        >
          + Add company
        </Link>
        <UserButton />
      </>
    );
  }

  return (
    <>
      <SignInButton mode="modal">
        <button className="cursor-pointer text-sm font-medium" type="button">
          Sign In
        </button>
      </SignInButton>
      <SignUpButton mode="modal">
        <button
          className="cursor-pointer rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background"
          type="button"
        >
          Sign Up
        </button>
      </SignUpButton>
    </>
  );
}

export function HomeAuthActions() {
  const { isLoaded, isSignedIn } = useAuth();

  if (!isLoaded || isSignedIn === undefined) {
    return <div aria-hidden="true" className="h-12 w-72" />;
  }

  if (isSignedIn) {
    return (
      <Link className="rounded-full bg-foreground px-6 py-3 font-medium text-background" href="/route-after-auth">
        Open workspace
      </Link>
    );
  }

  return (
    <>
      <SignUpButton mode="modal">
        <button className="cursor-pointer rounded-full bg-foreground px-6 py-3 font-medium text-background" type="button">
          Create workspace
        </button>
      </SignUpButton>
      <SignInButton mode="modal">
        <button
          className="cursor-pointer rounded-full border border-black/15 px-6 py-3 font-medium dark:border-white/15"
          type="button"
        >
          Sign in
        </button>
      </SignInButton>
    </>
  );
}
