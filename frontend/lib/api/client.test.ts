import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient, ApiError } from "./client.ts";

test("adds the Clerk bearer token and correlation headers", async () => {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    captured = init;
    return Response.json({ ok: true });
  };
  try {
    const api = new ApiClient(async () => "session-token", "http://api.test");
    await api.request("/protected", { method: "POST", body: "{}" }, "request-123");
    const headers = new Headers(captured?.headers);
    assert.equal(headers.get("Authorization"), "Bearer session-token");
    assert.equal(headers.get("Content-Type"), "application/json");
    assert.equal(headers.get("X-Request-ID"), "request-123");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fails locally when Clerk has no session token", async () => {
  const api = new ApiClient(async () => null, "http://api.test");
  await assert.rejects(
    () => api.request("/protected"),
    (error: unknown) => error instanceof ApiError && error.kind === "unauthenticated",
  );
});

test("parses the backend stable error envelope", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json(
    { error: { code: "ORGANIZATION_REQUIRED", message: "Select an organization.", details: {}, request_id: "req-1" } },
    { status: 403 },
  );
  try {
    const api = new ApiClient(async () => "session-token", "http://api.test");
    await assert.rejects(
      () => api.request("/protected"),
      (error: unknown) => error instanceof ApiError && error.kind === "organization_required" && error.requestId === "req-1",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
