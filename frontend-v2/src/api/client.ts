export type ApiErrorKind =
  | "unauthenticated"
  | "forbidden"
  | "conflict"
  | "validation"
  | "rate-limited"
  | "unavailable"
  | "network"
  | "timeout"
  | "cancelled"
  | "missing-context"
  | "invalid-response"
  | "unknown";

export type ApiErrorBody = {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
};
type Envelope<T> = {
  success: boolean;
  data: T | null;
  error: ApiErrorBody | null;
  request_id: string;
};

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId = "",
    public readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type RequestContext = {
  accessToken?: string;
  currentHouseId?: string | null;
};
export type RequestDescriptor = {
  authentication: "none" | "required";
  house: "none" | "required";
  decoder: "direct" | "envelope";
  invalidateSessionOn401: boolean;
};
export type RequestOptions = Omit<RequestInit, "body" | "signal"> & {
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  idempotencyKey?: string;
  requestId?: string;
};

const statusKinds: Partial<Record<number, ApiErrorKind>> = {
  401: "unauthenticated",
  403: "forbidden",
  409: "conflict",
  422: "validation",
  429: "rate-limited",
  503: "unavailable",
};

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getContext: () => RequestContext,
    private readonly fetcher: typeof fetch = fetch,
    private readonly onAuthenticatedUnauthorized: () => void | Promise<void> = () =>
      undefined,
  ) {}

  async request<T>(
    descriptor: RequestDescriptor,
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const context = this.requireContext(descriptor);
    const controller = new AbortController();
    let timedOut = false;
    const timeout = globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, options.timeoutMs ?? 10_000);
    const cancel = () => controller.abort();
    options.signal?.addEventListener("abort", cancel, { once: true });
    if (options.signal?.aborted) controller.abort();
    try {
      const fetcher = this.fetcher;
      const response = await fetcher(this.resolveUrl(path), {
        ...options,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        headers: this.headers(descriptor, context, options),
        signal: controller.signal,
      });
      return await this.parse<T>(descriptor, response);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (options.signal?.aborted)
        throw new ApiError("cancelled", 0, "REQUEST_CANCELLED", "请求已取消。");
      if (timedOut)
        throw new ApiError(
          "timeout",
          0,
          "REQUEST_TIMEOUT",
          "服务响应超时，请稍后重试。",
        );
      throw new ApiError(
        "network",
        0,
        "NETWORK_ERROR",
        "无法连接服务，请检查网络或稍后重试。",
      );
    } finally {
      globalThis.clearTimeout(timeout);
      options.signal?.removeEventListener("abort", cancel);
    }
  }

  private requireContext(descriptor: RequestDescriptor): RequestContext {
    const context = this.getContext();
    if (descriptor.authentication === "required" && !context.accessToken) {
      throw new ApiError(
        "missing-context",
        0,
        "AUTH_CONTEXT_REQUIRED",
        "缺少认证上下文。",
      );
    }
    if (descriptor.house === "required" && !context.currentHouseId) {
      throw new ApiError(
        "missing-context",
        0,
        "HOUSE_CONTEXT_REQUIRED",
        "请先选择当前房屋。",
      );
    }
    return context;
  }

  private resolveUrl(path: string): string {
    return `${this.baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
  }

  private headers(
    descriptor: RequestDescriptor,
    context: RequestContext,
    options: RequestOptions,
  ): Headers {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    headers.set(
      "X-Request-ID",
      options.requestId ?? `web_v2_${crypto.randomUUID()}`,
    );
    if (options.body !== undefined)
      headers.set("Content-Type", "application/json");
    if (descriptor.authentication === "required")
      headers.set("Authorization", `Bearer ${context.accessToken}`);
    if (descriptor.house === "required")
      headers.set("X-Current-House-ID", context.currentHouseId!);
    if (options.idempotencyKey)
      headers.set("Idempotency-Key", options.idempotencyKey);
    return headers;
  }

  private async parse<T>(
    descriptor: RequestDescriptor,
    response: Response,
  ): Promise<T> {
    if (response.status === 401 && descriptor.invalidateSessionOn401) {
      try {
        await this.onAuthenticatedUnauthorized();
      } catch {
        /* Session invalidation remains best-effort and cannot mask the 401. */
      }
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError(
        "invalid-response",
        response.status,
        "INVALID_RESPONSE",
        "服务返回了无法识别的响应。",
      );
    }
    if (!response.ok) {
      throw this.httpError(response.status, payload);
    }
    if (descriptor.decoder === "direct") {
      if (
        typeof payload !== "object" ||
        payload === null ||
        Array.isArray(payload)
      ) {
        throw new ApiError(
          "invalid-response",
          response.status,
          "INVALID_RESPONSE",
          "响应不符合 direct 契约。",
        );
      }
      return payload as T;
    }
    return this.parseEnvelope<T>(response.status, payload);
  }

  private parseEnvelope<T>(status: number, payload: unknown): T {
    if (
      typeof payload !== "object" ||
      payload === null ||
      typeof (payload as Partial<Envelope<T>>).success !== "boolean"
    ) {
      throw new ApiError(
        "invalid-response",
        status,
        "INVALID_RESPONSE",
        "响应不符合 API Envelope 契约。",
      );
    }
    const envelope = payload as Envelope<T>;
    if (envelope.success) return envelope.data as T;
    throw new ApiError(
      "invalid-response",
      status,
      envelope.error?.code ?? "INVALID_ENVELOPE",
      envelope.error?.message ?? "响应缺少业务数据。",
      envelope.request_id,
      envelope.error?.details ?? null,
    );
  }

  private httpError(status: number, payload: unknown): ApiError {
    const body =
      typeof payload === "object" && payload !== null
        ? (payload as Record<string, unknown>)
        : {};
    const nested =
      typeof body.error === "object" && body.error !== null
        ? (body.error as Record<string, unknown>)
        : {};
    const code =
      typeof nested.code === "string" ? nested.code : `HTTP_${status}`;
    const message =
      typeof nested.message === "string" && nested.message.trim()
        ? nested.message
        : "请求未成功。";
    const requestId =
      typeof body.request_id === "string" ? body.request_id : "";
    const details =
      typeof nested.details === "object" &&
      nested.details !== null &&
      !Array.isArray(nested.details)
        ? (nested.details as Record<string, unknown>)
        : null;
    return new ApiError(
      statusKinds[status] ?? "unknown",
      status,
      code,
      message,
      requestId,
      details,
    );
  }
}
