import {
  OrganizationSwitcher,
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="border-b border-black/10 bg-white px-6 py-4 dark:border-white/10 dark:bg-black">
      <nav className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4">
        <Link className="text-lg font-semibold tracking-tight" href="/">
          Stratix
        </Link>
        <div className="flex items-center gap-3">
          <Show when="signed-out">
            <SignInButton mode="modal">
              <button className="cursor-pointer text-sm font-medium">Sign In</button>
            </SignInButton>
            <SignUpButton mode="modal">
              <button className="cursor-pointer rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background">
                Sign Up
              </button>
            </SignUpButton>
          </Show>
          <Show when="signed-in">
            <OrganizationSwitcher
              afterCreateOrganizationUrl="/onboarding"
              afterSelectOrganizationUrl="/dashboard"
              hidePersonal
            />
            <UserButton />
          </Show>
        </div>
      </nav>
    </header>
  );
}
