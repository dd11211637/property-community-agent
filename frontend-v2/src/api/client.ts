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
  | "invalid-response"
  | "unknown";

export type ApiErrorBody = { code: string; message: string; details?: Record<string, unknown> | null };
type Envelope<T> = { success: boolean; data: T | null; error: ApiErrorBody | null; request_id: string };

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

export type RequestContext = { accessToken?: string; currentHouseId?: string | null };
export type RequestOptions = Omit<RequestInit, "body" | "signal"> & {
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  idempotencyKey?: string;
  expectedVersion?: number;
  confirmationToken?: string;
};

const statusKinds: Partial<Record<number, ApiErrorKind>> = {
  401: "unauthenticated", 403: "forbidden", 409: "conflict", 422: "validation", 429: "rate-limited", 503: "unavailable",
};

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getContext: () => RequestContext,
    private readonly fetcher: typeof fetch = fetch,
  ) {}

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, options.timeoutMs ?? 10_000);
    const cancel = () => controller.abort();
    options.signal?.addEventListener("abort", cancel, { once: true });
    try {
      const response = await this.fetcher(this.resolveUrl(path), {
        ...options,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        headers: this.headers(options),
        signal: controller.signal,
      });
      return await this.parse<T>(response);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (options.signal?.aborted) throw new ApiError("cancelled", 0, "REQUEST_CANCELLED", "请求已取消。");
      if (timedOut) throw new ApiError("timeout", 0, "REQUEST_TIMEOUT", "服务响应超时，请稍后重试。");
      throw new ApiError("network", 0, "NETWORK_ERROR", "无法连接服务，请检查网络或稍后重试。");
    } finally {
      window.clearTimeout(timeout);
      options.signal?.removeEventListener("abort", cancel);
    }
  }

  private resolveUrl(path: string): string {
    return `${this.baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
  }

  private headers(options: RequestOptions): Headers {
    const context = this.getContext();
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Request-ID", `web_v2_${crypto.randomUUID()}`);
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (context.accessToken) headers.set("Authorization", `Bearer ${context.accessToken}`);
    if (context.currentHouseId) headers.set("X-Current-House-ID", context.currentHouseId);
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
    return headers;
  }

  private async parse<T>(response: Response): Promise<T> {
    let payload: unknown;
    try { payload = await response.json(); } catch {
      throw new ApiError("invalid-response", response.status, "INVALID_RESPONSE", "服务返回了无法识别的响应。");
    }
    if (typeof payload !== "object" || payload === null || typeof (payload as Partial<Envelope<T>>).success !== "boolean") {
      if (response.ok) throw new ApiError("invalid-response", response.status, "INVALID_RESPONSE", "响应不符合 API Envelope 契约。");
      throw new ApiError(statusKinds[response.status] ?? "unknown", response.status, `HTTP_${response.status}`, "请求未成功。");
    }
    const envelope = payload as Envelope<T>;
    if (response.ok && envelope.success) return envelope.data as T;
    throw new ApiError(
      statusKinds[response.status] ?? "unknown",
      response.status,
      envelope.error?.code ?? `HTTP_${response.status}`,
      envelope.error?.message ?? "请求未成功。",
      envelope.request_id,
      envelope.error?.details ?? null,
    );
  }
}
