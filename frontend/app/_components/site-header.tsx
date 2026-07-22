import { HeaderAuthActions } from "@/app/_components/auth-actions";
import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="border-b border-black/10 bg-white px-6 py-4 dark:border-white/10 dark:bg-black">
      <nav className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4">
        <Link className="text-lg font-semibold tracking-tight" href="/">
          Stratix
        </Link>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-3">
          <HeaderAuthActions />
        </div>
      </nav>
    </header>
  );
}
