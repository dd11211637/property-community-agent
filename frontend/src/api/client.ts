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
  timeoutMs?: number;
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
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? 10_000;
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller.signal,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "REQUEST_TIMEOUT", "服务响应时间过长，请稍后重试。");
    }
    throw new ApiError(0, "NETWORK_ERROR", "无法连接服务，请检查网络或稍后重试。");
  } finally {
    window.clearTimeout(timeoutId);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, "INVALID_RESPONSE", "服务返回了无法识别的响应。");
  }
  const isEnvelope = typeof payload === "object" && payload !== null &&
    typeof (payload as Partial<Envelope<T>>).success === "boolean";
  if (isEnvelope) {
    const envelope = payload as Envelope<T>;
    if (response.ok && envelope.success && envelope.data !== null) return envelope.data;
    throw new ApiError(
      response.status,
      envelope.error?.code ?? `HTTP_${response.status}`,
      envelope.error?.message ?? "请求未成功。",
      envelope.request_id,
      envelope.error?.details ?? null,
    );
  }
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null
      ? (payload as { detail?: unknown }).detail
      : null;
    const statusMessages: Record<number, string> = {
      429: "操作过于频繁，请稍后再试。",
      502: "后端服务暂时不可用，请稍后重试。",
      503: "服务当前繁忙或维护中，请稍后重试。",
      504: "服务响应超时，请稍后重试。",
    };
    const message = typeof detail === "string"
      ? detail
      : statusMessages[response.status] ?? "请求未成功。";
    throw new ApiError(response.status, `HTTP_${response.status}`, message);
  }
  return payload as T;
}

export async function streamAgentTurn<T>(path: string, body: unknown): Promise<T> {
  const token = sessionStorage.getItem("property_agent_token");
  const houseId = sessionStorage.getItem("property_agent_house_id");
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
    "X-Request-ID": requestId(),
  });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (houseId) headers.set("X-Current-House-ID", houseId);
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "无法连接服务，请检查网络或稍后重试。");
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, `HTTP_${response.status}`, "Agent 流式请求未成功。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalTurn: T | undefined;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const parsed = parseSseBlock(block);
      if (parsed?.event === "turn") finalTurn = parsed.data as T;
      if (parsed?.event === "failed") {
        throw new ApiError(503, "AGENT_STREAM_FAILED", "Agent 执行失败，可刷新会话查看状态。");
      }
    }
    if (done) break;
  }
  if (finalTurn === undefined) {
    throw new ApiError(502, "STREAM_FINAL_MISSING", "流式响应缺少最终会话状态。");
  }
  return finalTurn;
}

function parseSseBlock(block: string): { event: string; data: unknown } | null {
  const lines = block.split(/\r?\n/);
  const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines.find((line) => line.startsWith("data:"))?.slice(5).trim();
  if (!event || data === undefined) return null;
  try {
    return { event, data: JSON.parse(data) as unknown };
  } catch {
    throw new ApiError(502, "INVALID_STREAM_EVENT", "服务返回了无法识别的流式事件。");
  }
}

export function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const result = query.toString();
  return result ? `?${result}` : "";
}
