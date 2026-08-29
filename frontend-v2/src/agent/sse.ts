import { ApiError } from "../api/client";
import {
  publicAgentEvents,
  type AgentStreamEvent,
  type PublicAgentEventName,
} from "./models";

const MAX_EVENT_BYTES = 256 * 1024;
const knownEvents = new Set<string>(publicAgentEvents);

function decodeEvent(frame: string): AgentStreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  const payloadText = data.join("\n");
  if (new TextEncoder().encode(payloadText).byteLength > MAX_EVENT_BYTES)
    throw new ApiError(
      "invalid-response",
      200,
      "SSE_EVENT_TOO_LARGE",
      "Agent 流事件超过安全大小限制。",
    );
  let payload: unknown;
  try {
    payload = JSON.parse(payloadText);
  } catch {
    throw new ApiError(
      "invalid-response",
      200,
      "INVALID_SSE_JSON",
      "Agent 流返回了无法识别的事件。",
    );
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload))
    throw new ApiError(
      "invalid-response",
      200,
      "INVALID_SSE_EVENT",
      "Agent 流事件结构无效。",
    );
  return {
    event: knownEvents.has(event)
      ? (event as PublicAgentEventName)
      : "unknown",
    originalEvent: event,
    data: payload as Record<string, unknown>,
  };
}

export async function* parseAgentSse(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  idleTimeoutMs = 60_000,
): AsyncGenerator<AgentStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted)
        throw new ApiError("cancelled", 0, "REQUEST_CANCELLED", "请求已取消。");
      let timer: ReturnType<typeof setTimeout> | undefined;
      const idle = new Promise<never>((_resolve, reject) => {
        timer = globalThis.setTimeout(() => reject(new ApiError(
          "timeout",
          0,
          "AGENT_STREAM_IDLE_TIMEOUT",
          "Agent 流长时间没有返回事件。",
        )), idleTimeoutMs);
      });
      let abortHandler: (() => void) | undefined;
      const aborted = new Promise<never>((_resolve, reject) => {
        if (!signal) return;
        abortHandler = () => {
          reject(new ApiError(
            "cancelled",
            0,
            "REQUEST_CANCELLED",
            "请求已取消。",
          ));
          void reader.cancel().catch(() => undefined);
        };
        signal.addEventListener("abort", abortHandler, { once: true });
      });
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await Promise.race([reader.read(), idle, aborted]);
      } catch (error) {
        await reader.cancel().catch(() => undefined);
        throw error;
      } finally {
        if (timer) globalThis.clearTimeout(timer);
        if (abortHandler) signal?.removeEventListener("abort", abortHandler);
      }
      const { value, done } = chunk;
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const event = decodeEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = decodeEvent(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}
