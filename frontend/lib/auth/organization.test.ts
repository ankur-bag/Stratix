import assert from "node:assert/strict";
import test from "node:test";
import { ensureActiveOrganization } from "./organization.ts";

test("reuses an active Organization and refreshes the token", async () => {
  let tokenRefreshed = false;
  const token = await ensureActiveOrganization({
    activeOrganizationId: "org_existing",
    companyName: "Existing",
    createOrganization: async () => { throw new Error("must not create"); },
    setActive: async () => { throw new Error("must not activate"); },
    getToken: async ({ skipCache }) => {
      tokenRefreshed = skipCache;
      return "fresh-token";
    },
  });
  assert.equal(token, "fresh-token");
  assert.equal(tokenRefreshed, true);
});

test("creates and activates an Organization before requesting the token", async () => {
  const events: string[] = [];
  const token = await ensureActiveOrganization({
    activeOrganizationId: null,
    companyName: "  Acme Labs  ",
    createOrganization: async ({ name }) => {
      events.push(`create:${name}`);
      return { id: "org_new" };
    },
    setActive: async ({ organization }) => { events.push(`activate:${organization}`); },
    getToken: async () => {
      events.push("token");
      return "fresh-token";
    },
  });
  assert.equal(token, "fresh-token");
  assert.deepEqual(events, ["create:Acme Labs", "activate:org_new", "token"]);
});
