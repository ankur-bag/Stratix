import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient, ApiError, type TokenProvider } from "./client.ts";

function client(
  getToken: TokenProvider,
  isCurrent: (organizationId: string) => boolean = () => true,
): ApiClient {
  return new ApiClient(getToken, "http://api.test", "org_acme", isCurrent);
}

test("adds the Clerk bearer token and correlation headers", async () => {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    captured = init;
    return Response.json({ ok: true });
  };
  try {
    let tokenOrganizationId: string | undefined;
    const api = client(async (options) => {
      tokenOrganizationId = options?.organizationId;
      return "session-token";
    });
    await api.request("/protected", { method: "POST", body: "{}" }, "request-123");
    const headers = new Headers(captured?.headers);
    assert.equal(headers.get("Authorization"), "Bearer session-token");
    assert.equal(headers.get("Content-Type"), "application/json");
    assert.equal(headers.get("X-Request-ID"), "request-123");
    assert.equal(tokenOrganizationId, "org_acme");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fails locally when Clerk has no session token", async () => {
  const api = client(async () => null);
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
    const api = client(async () => "session-token");
    await assert.rejects(
      () => api.request("/protected"),
      (error: unknown) => error instanceof ApiError && error.kind === "organization_required" && error.requestId === "req-1",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("retries one unauthorized response with a fresh Clerk token", async () => {
  const originalFetch = globalThis.fetch;
  const tokens: string[] = [];
  let requestCount = 0;
  globalThis.fetch = async (_input, init) => {
    requestCount += 1;
    tokens.push(new Headers(init?.headers).get("Authorization") ?? "");
    return requestCount === 1
      ? Response.json({ error: { code: "INVALID_SESSION_TOKEN" } }, { status: 401 })
      : Response.json({ ok: true });
  };
  let tokenCount = 0;
  try {
    const api = client(async () => {
      tokenCount += 1;
      return tokenCount === 1 ? "stale-token" : "fresh-token";
    });
    await api.request("/protected");
    assert.deepEqual(tokens, ["Bearer stale-token", "Bearer fresh-token"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("source creation uses a fresh Clerk token and encoded competitor path", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  let tokenCalls = 0;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json({ id: "source-1" });
  };
  try {
    const api = client(async () => {
      tokenCalls += 1;
      return "fresh-session-token";
    });
    await api.createCompetitorSource("competitor/1", {
      url: "https://acme.example/pricing",
      type: "pricing",
    });
    assert.equal(tokenCalls, 1);
    assert.equal(capturedUrl, "http://api.test/api/v1/competitors/competitor%2F1/sources");
    assert.equal(new Headers(captured?.headers).get("Authorization"), "Bearer fresh-session-token");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("scan writes include an idempotency key", async () => {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    captured = init;
    return Response.json({ scan: { id: "scan-1" } }, { status: 202 });
  };
  try {
    const api = client(async () => "session-token");
    await api.startCompetitorScan(
      "competitor-1",
      { scan_type: "rescan", rediscover_sources: false },
      "scan:stable-key",
    );
    assert.equal(
      new Headers(captured?.headers).get("Idempotency-Key"),
      "scan:stable-key",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fails before fetching when no active organization exists", async () => {
  const api = new ApiClient(async () => "session-token", "http://api.test", null);
  await assert.rejects(
    () => api.request("/protected"),
    (error: unknown) =>
      error instanceof ApiError &&
      error.kind === "organization_required" &&
      error.code === "ORGANIZATION_REQUIRED",
  );
});

test("rejects a response when the active organization changes mid-request", async () => {
  const originalFetch = globalThis.fetch;
  let currentOrganizationId = "org_acme";
  let release: (() => void) | undefined;
  globalThis.fetch = async () => {
    await new Promise<void>((resolve) => { release = resolve; });
    return Response.json({ company: "Acme" });
  };
  try {
    const api = client(
      async () => "acme-token",
      (organizationId) => currentOrganizationId === organizationId,
    );
    const request = api.request("/protected");
    await new Promise((resolve) => setTimeout(resolve, 0));
    currentOrganizationId = "org_beta";
    release?.();
    await assert.rejects(
      () => request,
      (error: unknown) =>
        error instanceof ApiError &&
        error.kind === "organization_switch" &&
        error.code === "ORGANIZATION_SWITCHED",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("reports aborted requests separately from network failures", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new DOMException("Aborted", "AbortError");
  };
  try {
    const api = client(async () => "session-token");
    await assert.rejects(
      () => api.request("/protected"),
      (error: unknown) => error instanceof ApiError && error.kind === "cancelled",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
