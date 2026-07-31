import type {
  Competitor,
  CompetitorScan,
  CompetitorScanPage,
  CompetitorScanResponse,
  CompetitorSignal,
  CompetitorSource,
  CompetitorSourceCreate,
  CompetitorSourcePage,
  CompetitorSourceUpdate,
  CompetitorTimelinePage,
  CompanyDocument,
  DocumentIngestionJob,
  DocumentUploadResponse,
  MonitoringCadence,
  MonitoringSchedule,
  Notification,
  NotificationPage,
  ReinitializeResponse,
  SignalPage,
  IntelligenceAnswerResponse,
  IntelligenceIndexingStatus,
  IntelligenceOverview,
  IntelligenceReindexResponse,
  IntelligenceSearchInput,
  IntelligenceSearchResponse,
} from "./types";

export type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string;
  };
};

export type TokenOptions = {
  organizationId?: string;
  skipCache?: boolean;
};

export type TokenProvider = (options?: TokenOptions) => Promise<string | null>;

export type ApiErrorKind =
  | "unauthenticated"
  | "organization_required"
  | "organization_switch"
  | "cancelled"
  | "forbidden"
  | "validation"
  | "network"
  | "backend";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly code: string,
    readonly status: number | null,
    readonly requestId: string | null = null,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiClient {
  constructor(
    private readonly getToken: TokenProvider,
    private readonly baseUrl =
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
    readonly organizationId: string | null = null,
    private readonly isOrganizationCurrent: (organizationId: string) => boolean = () => true,
  ) {}

  async request<T>(
    path: string,
    init: RequestInit = {},
    requestId?: string,
  ): Promise<T> {
    const organizationId = this.requireOrganization();
    this.assertCurrent(organizationId);

    const token = await this.getToken({ organizationId, skipCache: true });
    this.assertCurrent(organizationId);
    if (!token) {
      throw new ApiError(
        "Sign in to continue.",
        "unauthenticated",
        "AUTHENTICATION_REQUIRED",
        401,
      );
    }

    let response: Response;
    try {
      response = await this.fetchWithToken(path, init, requestId, token);
      this.assertCurrent(organizationId);
      if (response.status === 401) {
        const refreshedToken = await this.getToken({ organizationId, skipCache: true });
        this.assertCurrent(organizationId);
        if (refreshedToken) {
          response = await this.fetchWithToken(path, init, requestId, refreshedToken);
          this.assertCurrent(organizationId);
        }
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (isAbortError(error)) {
        throw new ApiError(
          "The request was cancelled.",
          "cancelled",
          "REQUEST_CANCELLED",
          null,
        );
      }
      throw new ApiError(
        "The Stratix API could not be reached.",
        "network",
        "NETWORK_ERROR",
        null,
      );
    }

    if (!response.ok) {
      throw await this.toApiError(response);
    }
    if (response.status === 204) return undefined as T;
    const body = (await response.json()) as T;
    this.assertCurrent(organizationId);
    return body;
  }

  private requireOrganization(): string {
    if (this.organizationId) return this.organizationId;
    throw new ApiError(
      "Select a company workspace to continue.",
      "organization_required",
      "ORGANIZATION_REQUIRED",
      403,
    );
  }

  private assertCurrent(organizationId: string): void {
    if (!this.isOrganizationCurrent(organizationId)) {
      throw new ApiError(
        "The active company changed while this request was running.",
        "organization_switch",
        "ORGANIZATION_SWITCHED",
        null,
      );
    }
  }

  private fetchWithToken(
    path: string,
    init: RequestInit,
    requestId: string | undefined,
    token: string,
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    if (init.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (requestId) headers.set("X-Request-ID", requestId);
    return fetch(`${this.baseUrl}${path}`, { ...init, headers });
  }

  listCompetitors(signal?: AbortSignal): Promise<Competitor[]> {
    return this.request("/api/v1/competitors", { signal });
  }

  reinitializeCompany(
    idempotencyKey = createIdempotencyKey("reinitialize"),
  ): Promise<ReinitializeResponse> {
    return this.request("/api/v1/company/reinitialize", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  }

  getCompetitor(competitorId: string, signal?: AbortSignal): Promise<Competitor> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}`, { signal });
  }

  listCompetitorSources(
    competitorId: string,
    signal?: AbortSignal,
  ): Promise<CompetitorSourcePage> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}/sources`, {
      signal,
    });
  }

  createCompetitorSource(
    competitorId: string,
    input: CompetitorSourceCreate,
  ): Promise<CompetitorSource> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}/sources`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  updateCompetitorSource(
    competitorId: string,
    sourceId: string,
    input: CompetitorSourceUpdate,
  ): Promise<CompetitorSource> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "PATCH", body: JSON.stringify(input) },
    );
  }

  disableCompetitorSource(competitorId: string, sourceId: string): Promise<void> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    );
  }

  discoverCompetitorSources(
    competitorId: string,
    idempotencyKey = createIdempotencyKey("discover"),
  ): Promise<CompetitorScanResponse> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/sources/discover`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey } },
    );
  }

  startCompetitorScan(
    competitorId: string,
    input: { scan_type: "baseline" | "rescan"; rediscover_sources: boolean },
    idempotencyKey = createIdempotencyKey("scan"),
  ): Promise<CompetitorScanResponse> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}/scans`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(input),
    });
  }

  getCompetitorScan(scanId: string, signal?: AbortSignal): Promise<CompetitorScan> {
    return this.request(`/api/v1/competitor-scans/${encodeURIComponent(scanId)}`, { signal });
  }

  listCompetitorScans(
    competitorId: string,
    signal?: AbortSignal,
  ): Promise<CompetitorScanPage> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}/scans`, {
      signal,
    });
  }

  listCompetitorSignals(competitorId: string, signal?: AbortSignal): Promise<SignalPage> {
    return this.request(`/api/v1/competitors/${encodeURIComponent(competitorId)}/signals`, {
      signal,
    });
  }

  getSignal(signalId: string, signal?: AbortSignal): Promise<CompetitorSignal> {
    return this.request(`/api/v1/signals/${encodeURIComponent(signalId)}`, { signal });
  }

  updateSignalStatus(
    signalId: string,
    status: "reviewed" | "dismissed",
  ): Promise<CompetitorSignal> {
    return this.request(`/api/v1/signals/${encodeURIComponent(signalId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
  }

  createDocumentUpload(file: File): Promise<DocumentUploadResponse> {
    return this.request("/api/v1/company-documents/upload-requests", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content_type: "application/pdf",
        size_bytes: file.size,
      }),
    });
  }

  async uploadDocumentContent(
    target: DocumentUploadResponse["upload"],
    file: File,
    signal?: AbortSignal,
  ): Promise<void> {
    const headers = new Headers(target.headers);
    headers.set("Content-Type", "application/pdf");
    if (/^https?:\/\//i.test(target.url)) {
      let targetOrigin: string;
      let apiOrigin: string;
      try {
        targetOrigin = new URL(target.url).origin;
        apiOrigin = new URL(this.baseUrl).origin;
      } catch {
        throw new ApiError("The upload destination is invalid.", "backend", "DOCUMENT_UPLOAD_TARGET_INVALID", null);
      }
      if (targetOrigin !== apiOrigin) {
        let response: Response;
        try {
          response = await fetch(target.url, {
            method: target.method,
            headers,
            body: file,
            signal,
          });
        } catch (error) {
          if (isAbortError(error)) {
            throw new ApiError("The request was cancelled.", "cancelled", "REQUEST_CANCELLED", null);
          }
          throw new ApiError("The PDF upload failed.", "network", "DOCUMENT_UPLOAD_FAILED", null);
        }
        if (!response.ok) {
          throw new ApiError("The PDF upload failed.", "network", "DOCUMENT_UPLOAD_FAILED", response.status);
        }
        return;
      }
      target = { ...target, url: new URL(target.url).pathname };
    }
    await this.request<CompanyDocument>(
      target.url,
      { method: target.method, headers, body: file, signal },
    );
  }

  completeDocumentUpload(
    documentId: string,
    sha256: string,
    sizeBytes: number,
  ): Promise<CompanyDocument> {
    return this.request(`/api/v1/company-documents/${encodeURIComponent(documentId)}/complete`, {
      method: "POST",
      body: JSON.stringify({ sha256, size_bytes: sizeBytes }),
    });
  }

  listCompanyDocuments(signal?: AbortSignal): Promise<{ items: CompanyDocument[] }> {
    return this.request("/api/v1/company-documents", { signal });
  }

  getDocumentIngestionJob(
    jobId: string,
    signal?: AbortSignal,
  ): Promise<DocumentIngestionJob> {
    return this.request(`/api/v1/document-ingestion-jobs/${encodeURIComponent(jobId)}`, { signal });
  }

  reprocessCompanyDocument(documentId: string): Promise<DocumentIngestionJob> {
    return this.request(`/api/v1/company-documents/${encodeURIComponent(documentId)}/reprocess`, {
      method: "POST",
    });
  }

  deleteCompanyDocument(documentId: string): Promise<void> {
    return this.request(`/api/v1/company-documents/${encodeURIComponent(documentId)}`, {
      method: "DELETE",
    });
  }

  getMonitoringSchedule(
    competitorId: string,
    signal?: AbortSignal,
  ): Promise<MonitoringSchedule> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/monitoring-schedule`,
      { signal },
    );
  }

  updateMonitoringSchedule(
    competitorId: string,
    input: {
      is_enabled: boolean;
      cadence: MonitoringCadence;
      timezone?: string;
    },
  ): Promise<MonitoringSchedule> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/monitoring-schedule`,
      {
        method: "PATCH",
        body: JSON.stringify({ timezone: "UTC", ...input }),
      },
    );
  }

  runMonitoringNow(competitorId: string): Promise<CompetitorScanResponse> {
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/monitoring-schedule/run-now`,
      { method: "POST" },
    );
  }

  listNotifications(
    signal?: AbortSignal,
    options: { unreadOnly?: boolean; cursor?: string; limit?: number } = {},
  ): Promise<NotificationPage> {
    const params = new URLSearchParams();
    if (options.unreadOnly) params.set("unread_only", "true");
    if (options.cursor) params.set("cursor", options.cursor);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.size ? `?${params.toString()}` : "";
    return this.request(`/api/v1/notifications${query}`, { signal });
  }

  getNotificationUnreadCount(signal?: AbortSignal): Promise<{ unread_count: number }> {
    return this.request("/api/v1/notifications/unread-count", { signal });
  }

  markNotificationRead(notificationId: string): Promise<Notification> {
    return this.request(
      `/api/v1/notifications/${encodeURIComponent(notificationId)}/read`,
      { method: "POST" },
    );
  }

  markAllNotificationsRead(): Promise<{ unread_count: number }> {
    return this.request("/api/v1/notifications/read-all", { method: "POST" });
  }

  searchIntelligence(
    input: IntelligenceSearchInput,
    signal?: AbortSignal,
  ): Promise<IntelligenceSearchResponse> {
    return this.request("/api/v1/intelligence/search", {
      method: "POST",
      body: JSON.stringify(input),
      signal,
    });
  }

  answerIntelligence(
    input: IntelligenceSearchInput,
    signal?: AbortSignal,
  ): Promise<IntelligenceAnswerResponse> {
    return this.request("/api/v1/intelligence/answer", {
      method: "POST",
      body: JSON.stringify(input),
      signal,
    });
  }

  getIntelligenceOverview(signal?: AbortSignal): Promise<IntelligenceOverview> {
    return this.request("/api/v1/intelligence/overview", { signal });
  }

  getCompetitorTimeline(
    competitorId: string,
    options: { cursor?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<CompetitorTimelinePage> {
    const params = new URLSearchParams();
    if (options.cursor) params.set("cursor", options.cursor);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.size ? `?${params.toString()}` : "";
    return this.request(
      `/api/v1/competitors/${encodeURIComponent(competitorId)}/timeline${query}`,
      { signal },
    );
  }

  getIntelligenceIndexingStatus(
    signal?: AbortSignal,
  ): Promise<IntelligenceIndexingStatus> {
    return this.request("/api/v1/intelligence/indexing-status", { signal });
  }

  reindexIntelligence(competitorIds: string[] = []): Promise<IntelligenceReindexResponse> {
    return this.request("/api/v1/intelligence/reindex", {
      method: "POST",
      body: JSON.stringify({ competitor_ids: competitorIds }),
    });
  }

  private async toApiError(response: Response): Promise<ApiError> {
    let envelope: ApiErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // A proxy or server may fail before returning Stratix's stable envelope.
    }
    const code = envelope?.error.code ?? "BACKEND_ERROR";
    const kind: ApiErrorKind =
      response.status === 401
        ? "unauthenticated"
        : code === "ORGANIZATION_REQUIRED"
          ? "organization_required"
          : response.status === 403
            ? "forbidden"
            : response.status === 422
              ? "validation"
              : "backend";
    return new ApiError(
      envelope?.error.message ?? "The request could not be completed.",
      kind,
      code,
      response.status,
      envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
      envelope?.error.details,
    );
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function createIdempotencyKey(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}

export function createApiClient(
  getToken: TokenProvider,
  organizationId: string | null,
): ApiClient {
  return new ApiClient(getToken, undefined, organizationId);
}
