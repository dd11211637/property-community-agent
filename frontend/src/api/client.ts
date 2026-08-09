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

export type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  idempotencyKey?: string;
};

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function requestId(): string {
  return `web_${crypto.randomUUID()}`;
}

export function createIdempotencyKey(operation: string): string {
  return `${operation}_${crypto.randomUUID()}`;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = sessionStorage.getItem("property_agent_token");
  const houseId = sessionStorage.getItem("property_agent_house_id");
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", requestId());
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (houseId) headers.set("X-Current-House-ID", houseId);

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "无法连接服务，请检查网络或稍后重试。");
  }

  let envelope: Envelope<T>;
  try {
    envelope = (await response.json()) as Envelope<T>;
  } catch {
    throw new ApiError(response.status, "INVALID_RESPONSE", "服务返回了无法识别的响应。");
  }
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new ApiError(
      response.status,
      envelope.error?.code ?? `HTTP_${response.status}`,
      envelope.error?.message ?? "请求未成功。",
      envelope.request_id,
      envelope.error?.details ?? null,
    );
  }
  return envelope.data;
}

export function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const result = query.toString();
  return result ? `?${result}` : "";
}
