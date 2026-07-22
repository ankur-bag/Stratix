export type OrganizationCreator = (input: { name: string }) => Promise<{ id: string }>;
export type OrganizationActivator = (input: { organization: string }) => Promise<void>;

export class CompanyWorkspaceError extends Error {
  constructor(
    message: string,
    readonly code:
      | "INVALID_COMPANY_NAME"
      | "INVALID_COMPANY_WEBSITE"
      | "ORGANIZATION_CONTROLS_UNAVAILABLE"
      | "ORGANIZATION_ACTIVATION_TIMEOUT",
  ) {
    super(message);
    this.name = "CompanyWorkspaceError";
  }
}

export function validateCompanyName(value: string): string {
  const name = value.trim().replace(/\s+/g, " ");
  if (name.length < 2 || name.length > 80) {
    throw new CompanyWorkspaceError(
      "Company name must be between 2 and 80 characters.",
      "INVALID_COMPANY_NAME",
    );
  }
  return name;
}

export function normalizeOptionalWebsite(value: string): string | null {
  const website = value.trim();
  if (!website) return null;
  let parsed: URL;
  try {
    parsed = new URL(website);
  } catch {
    throw new CompanyWorkspaceError(
      "Enter a valid public website beginning with http:// or https://.",
      "INVALID_COMPANY_WEBSITE",
    );
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new CompanyWorkspaceError(
      "Enter a valid public website beginning with http:// or https://.",
      "INVALID_COMPANY_WEBSITE",
    );
  }
  return parsed.toString();
}

export async function activateOrganization({
  organizationId,
  setActive,
  getActiveOrganizationId,
  attempts = 20,
  delay = defaultDelay,
}: {
  organizationId: string;
  setActive: OrganizationActivator | undefined;
  getActiveOrganizationId: () => string | null;
  attempts?: number;
  delay?: (milliseconds: number) => Promise<void>;
}): Promise<void> {
  if (!setActive) {
    throw new CompanyWorkspaceError(
      "Company controls are still loading. Try again in a moment.",
      "ORGANIZATION_CONTROLS_UNAVAILABLE",
    );
  }
  await setActive({ organization: organizationId });
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (getActiveOrganizationId() === organizationId) return;
    await delay(50);
  }
  throw new CompanyWorkspaceError(
    "The company switch did not finish. Please try again.",
    "ORGANIZATION_ACTIVATION_TIMEOUT",
  );
}

function defaultDelay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
