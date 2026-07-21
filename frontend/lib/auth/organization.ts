export type OrganizationCreator = (input: { name: string }) => Promise<{ id: string }>;
export type OrganizationActivator = (input: { organization: string }) => Promise<void>;
export type FreshTokenProvider = (input: { skipCache: boolean }) => Promise<string | null>;

export async function ensureActiveOrganization({
  activeOrganizationId,
  companyName,
  createOrganization,
  setActive,
  getToken,
}: {
  activeOrganizationId: string | null;
  companyName: string;
  createOrganization: OrganizationCreator | undefined;
  setActive: OrganizationActivator | undefined;
  getToken: FreshTokenProvider;
}): Promise<string> {
  if (!activeOrganizationId) {
    if (!createOrganization || !setActive) {
      throw new Error("Organization controls are not ready");
    }
    const created = await createOrganization({ name: companyName.trim() });
    await setActive({ organization: created.id });
  }
  const token = await getToken({ skipCache: true });
  if (!token) throw new Error("Session token is unavailable");
  return token;
}
