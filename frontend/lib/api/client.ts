export type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string;
  };
};

export type TokenProvider = () => Promise<string | null>;

export type ApiErrorKind =
  | "unauthenticated"
  | "organization_required"
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
  ) {}

  async request<T>(
    path: string,
    init: RequestInit = {},
    requestId?: string,
  ): Promise<T> {
    const token = await this.getToken();
    if (!token) {
      throw new ApiError(
        "Sign in to continue.",
        "unauthenticated",
        "AUTHENTICATION_REQUIRED",
        401,
      );
    }

    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("Content-Type", "application/json");
    if (requestId) headers.set("X-Request-ID", requestId);

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
      });
    } catch {
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
    return (await response.json()) as T;
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

export function createApiClient(getToken: TokenProvider): ApiClient {
  return new ApiClient(getToken);
}
