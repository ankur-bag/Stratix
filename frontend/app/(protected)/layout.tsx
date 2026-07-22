import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { CompanyWorkspaceBoundary } from "@/app/_components/company-workspace-boundary";

export default async function ProtectedLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const authState = await auth();

  if (!authState.isAuthenticated) {
    redirect("/sign-in");
  }

  return <CompanyWorkspaceBoundary>{children}</CompanyWorkspaceBoundary>;
}
