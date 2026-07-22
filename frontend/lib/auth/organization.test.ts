import assert from "node:assert/strict";
import test from "node:test";
import {
  activateOrganization,
  CompanyWorkspaceError,
  normalizeOptionalWebsite,
  validateCompanyName,
} from "./organization.ts";

test("normalizes and validates company creation fields", () => {
  assert.equal(validateCompanyName("  Acme   Labs  "), "Acme Labs");
  assert.equal(normalizeOptionalWebsite(""), null);
  assert.equal(normalizeOptionalWebsite("https://acme.test"), "https://acme.test/");
  assert.throws(
    () => validateCompanyName("A"),
    (error: unknown) =>
      error instanceof CompanyWorkspaceError && error.code === "INVALID_COMPANY_NAME",
  );
  assert.throws(
    () => normalizeOptionalWebsite("ftp://acme.test"),
    (error: unknown) =>
      error instanceof CompanyWorkspaceError && error.code === "INVALID_COMPANY_WEBSITE",
  );
});

test("activates a selected organization and waits until Clerk reflects it", async () => {
  const events: string[] = [];
  let activeOrganizationId: string | null = null;
  await activateOrganization({
    organizationId: "org_new",
    setActive: async ({ organization }) => { events.push(`activate:${organization}`); },
    getActiveOrganizationId: () => activeOrganizationId,
    delay: async () => { activeOrganizationId = "org_new"; },
  });
  assert.deepEqual(events, ["activate:org_new"]);
});

test("returns a stable timeout when activation never settles", async () => {
  await assert.rejects(
    () => activateOrganization({
      organizationId: "org_new",
      setActive: async () => undefined,
      getActiveOrganizationId: () => null,
      attempts: 2,
      delay: async () => undefined,
    }),
    (error: unknown) =>
      error instanceof CompanyWorkspaceError &&
      error.code === "ORGANIZATION_ACTIVATION_TIMEOUT",
  );
});
