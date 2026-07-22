import { SignIn } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

export default async function SignInPage() {
  const { userId } = await auth();

  if (userId) {
    redirect("/route-after-auth");
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <SignIn fallbackRedirectUrl="/route-after-auth" />
    </main>
  );
}
