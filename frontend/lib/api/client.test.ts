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

test("monitoring schedule writes are organization scoped without tenant fields", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; method: string | undefined; body: string }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({
      url: String(input),
      method: init?.method,
      body: String(init?.body ?? ""),
    });
    return Response.json({
      id: "schedule-1",
      competitor_id: "competitor/1",
      cadence: "daily",
      timezone: "UTC",
      is_enabled: true,
    });
  };
  try {
    const api = client(async () => "organization-token");
    await api.getMonitoringSchedule("competitor/1");
    await api.updateMonitoringSchedule("competitor/1", {
      cadence: "daily",
      is_enabled: true,
    });

    assert.deepEqual(
      requests.map((request) => [request.url, request.method]),
      [
        [
          "http://api.test/api/v1/competitors/competitor%2F1/monitoring-schedule",
          undefined,
        ],
        [
          "http://api.test/api/v1/competitors/competitor%2F1/monitoring-schedule",
          "PATCH",
        ],
      ],
    );
    const body = JSON.parse(requests[1].body) as Record<string, unknown>;
    assert.deepEqual(body, {
      timezone: "UTC",
      cadence: "daily",
      is_enabled: true,
    });
    assert.equal("organization_id" in body, false);
    assert.equal("company_profile_id" in body, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("run-now queues a durable competitor scan", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json(
      { scan: { id: "scan-1" }, job: { id: "job-1", status: "queued" } },
      { status: 202 },
    );
  };
  try {
    const api = client(async () => "organization-token");
    const response = await api.runMonitoringNow("competitor/1");
    assert.equal(response.job?.id, "job-1");
    assert.equal(
      capturedUrl,
      "http://api.test/api/v1/competitors/competitor%2F1/monitoring-schedule/run-now",
    );
    assert.equal(captured?.method, "POST");
    assert.equal(
      new Headers(captured?.headers).get("Authorization"),
      "Bearer organization-token",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("notification reads and mutations use encoded organization-scoped routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; method: string | undefined }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method });
    if (String(input).endsWith("/unread-count")) {
      return Response.json({ unread_count: 2 });
    }
    if (String(input).endsWith("/read-all")) {
      return Response.json({ unread_count: 0 });
    }
    if (String(input).endsWith("/read")) {
      return Response.json({ id: "notification/1", is_read: true });
    }
    return Response.json({ items: [], next_cursor: null });
  };
  try {
    const api = client(async () => "organization-token");
    await api.listNotifications(undefined, { unreadOnly: true, limit: 20 });
    await api.getNotificationUnreadCount();
    await api.markNotificationRead("notification/1");
    await api.markAllNotificationsRead();
    assert.deepEqual(requests, [
      {
        url: "http://api.test/api/v1/notifications?unread_only=true&limit=20",
        method: undefined,
      },
      {
        url: "http://api.test/api/v1/notifications/unread-count",
        method: undefined,
      },
      {
        url: "http://api.test/api/v1/notifications/notification%2F1/read",
        method: "POST",
      },
      {
        url: "http://api.test/api/v1/notifications/read-all",
        method: "POST",
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("company reinitialization uses an organization-scoped idempotent write", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json(
      {
        initialization_job_id: "job-retry",
        status: "queued",
        stage: "queued",
        progress_percent: 0,
      },
      { status: 202 },
    );
  };
  try {
    const api = client(async () => "session-token");
    const response = await api.reinitializeCompany("reinitialize:stable-key");
    assert.equal(response.initialization_job_id, "job-retry");
    assert.equal(capturedUrl, "http://api.test/api/v1/company/reinitialize");
    assert.equal(captured?.method, "POST");
    assert.equal(
      new Headers(captured?.headers).get("Idempotency-Key"),
      "reinitialize:stable-key",
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

test("document upload request is organization-authenticated and contains no provider fields", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json({
      document: { id: "document-1" },
      upload: { method: "PUT", url: "/api/v1/company-documents/document-1/content", headers: {} },
    }, { status: 201 });
  };
  try {
    const api = client(async () => "organization-token");
    await api.createDocumentUpload(new File(["%PDF-test"], "brief.pdf", { type: "application/pdf" }));
    assert.equal(capturedUrl, "http://api.test/api/v1/company-documents/upload-requests");
    assert.equal(new Headers(captured?.headers).get("Authorization"), "Bearer organization-token");
    const body = JSON.parse(String(captured?.body)) as Record<string, unknown>;
    assert.deepEqual(body, {
      filename: "brief.pdf",
      content_type: "application/pdf",
      size_bytes: 9,
    });
    assert.equal("organization_id" in body, false);
    assert.equal("namespace" in body, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("presigned document upload does not send Clerk authorization cross-origin", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return new Response(null, { status: 200 });
  };
  try {
    const api = client(async () => "organization-token");
    await api.uploadDocumentContent(
      {
        method: "PUT",
        url: "https://private-storage.example/upload",
        headers: { "x-upload-scope": "opaque" },
      },
      new File(["%PDF-test"], "brief.pdf", { type: "application/pdf" }),
    );
    assert.equal(capturedUrl, "https://private-storage.example/upload");
    assert.equal(new Headers(captured?.headers).get("Authorization"), null);
    assert.equal(new Headers(captured?.headers).get("x-upload-scope"), "opaque");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("document completion uses encoded id and active organization token", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json({ id: "document/1", status: "uploaded" });
  };
  try {
    const api = client(async () => "organization-token");
    await api.completeDocumentUpload("document/1", "a".repeat(64), 1024);
    assert.equal(capturedUrl, "http://api.test/api/v1/company-documents/document%2F1/complete");
    assert.equal(new Headers(captured?.headers).get("Authorization"), "Bearer organization-token");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("document retry and delete actions remain organization scoped", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; method: string | undefined; token: string | null }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({
      url: String(input),
      method: init?.method,
      token: new Headers(init?.headers).get("Authorization"),
    });
    return init?.method === "DELETE"
      ? new Response(null, { status: 204 })
      : Response.json({ id: "job-1", status: "queued" }, { status: 202 });
  };
  try {
    const api = client(async () => "organization-token");
    await api.reprocessCompanyDocument("document/1");
    await api.deleteCompanyDocument("document/1");
    assert.deepEqual(requests, [
      {
        url: "http://api.test/api/v1/company-documents/document%2F1/reprocess",
        method: "POST",
        token: "Bearer organization-token",
      },
      {
        url: "http://api.test/api/v1/company-documents/document%2F1",
        method: "DELETE",
        token: "Bearer organization-token",
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("cancelled cross-origin upload reports cancellation without leaking a token", async () => {
  const originalFetch = globalThis.fetch;
  let capturedAuthorization: string | null = "not-called";
  globalThis.fetch = async (_input, init) => {
    capturedAuthorization = new Headers(init?.headers).get("Authorization");
    throw new DOMException("Aborted", "AbortError");
  };
  try {
    const api = client(async () => "organization-token");
    await assert.rejects(
      () =>
        api.uploadDocumentContent(
          { method: "PUT", url: "https://private-storage.example/upload", headers: {} },
          new File(["%PDF-test"], "brief.pdf", { type: "application/pdf" }),
        ),
      (error: unknown) =>
        error instanceof ApiError &&
        error.kind === "cancelled" &&
        error.code === "REQUEST_CANCELLED",
    );
    assert.equal(capturedAuthorization, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("intelligence search serializes only supported organization-scoped filters", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json({
      query: "pricing",
      bundle: {
        items: [],
        grouped_by_competitor: {},
        coverage: {
          competitor_count: 0,
          source_type_count: 0,
          category_count: 0,
          earliest_observed_at: null,
          latest_observed_at: null,
        },
        warnings: [],
      },
    });
  };
  try {
    const api = client(async () => "organization-token");
    await api.searchIntelligence({
      query: "pricing",
      competitor_ids: ["competitor-1"],
      categories: ["pricing"],
    });
    assert.equal(capturedUrl, "http://api.test/api/v1/intelligence/search");
    assert.deepEqual(JSON.parse(String(captured?.body)), {
      query: "pricing",
      competitor_ids: ["competitor-1"],
      categories: ["pricing"],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("intelligence search forwards cancellation as a cancelled API error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new DOMException("Aborted", "AbortError");
  };
  try {
    const api = client(async () => "organization-token");
    await assert.rejects(
      () => api.searchIntelligence({ query: "pricing" }, new AbortController().signal),
      (error: unknown) => error instanceof ApiError && error.kind === "cancelled",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("reindex queues one durable workspace job without tenant fields", async () => {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    captured = init;
    return Response.json({ job_id: "job-1", status: "queued" }, { status: 202 });
  };
  try {
    const api = client(async () => "organization-token");
    await api.reindexIntelligence(["competitor-1"]);
    assert.deepEqual(JSON.parse(String(captured?.body)), {
      competitor_ids: ["competitor-1"],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("competitor timeline pagination remains bounded and organization authenticated", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";
  let captured: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    captured = init;
    return Response.json({ items: [], next_cursor: null });
  };
  try {
    const api = client(async () => "organization-token");
    await api.getCompetitorTimeline("competitor/1", {
      cursor: "evidence/10",
      limit: 10,
    });
    assert.equal(
      capturedUrl,
      "http://api.test/api/v1/competitors/competitor%2F1/timeline?cursor=evidence%2F10&limit=10",
    );
    assert.equal(
      new Headers(captured?.headers).get("Authorization"),
      "Bearer organization-token",
    );
    assert.equal(capturedUrl.includes("organization_id"), false);
    assert.equal(capturedUrl.includes("namespace"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
