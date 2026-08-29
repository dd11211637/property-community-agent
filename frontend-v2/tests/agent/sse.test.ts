import { describe, expect, it } from "vitest";
import { parseAgentSse } from "../../src/agent/sse";

function stream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function collect(chunks: Uint8Array[]) {
  const result = [];
  for await (const event of parseAgentSse(stream(chunks))) result.push(event);
  return result;
}

describe("parseAgentSse", () => {
  it("handles split frames, multiple events and unknown event names", async () => {
    const encoder = new TextEncoder();
    const value = encoder.encode(
      'event: message\ndata: {"message":"你好"}\n\nevent: future\ndata: {"safe":true}\n\n',
    );
    const events = await collect([value.slice(0, 17), value.slice(17, 39), value.slice(39)]);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ event: "message", data: { message: "你好" } });
    expect(events[1]).toMatchObject({ event: "unknown", originalEvent: "future" });
  });

  it("joins multiline data and preserves UTF-8 split boundaries", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode('event: facts\ndata: {"label":\ndata: "房屋"}\n\n');
    const marker = bytes.indexOf(0xe6);
    const events = await collect([bytes.slice(0, marker + 1), bytes.slice(marker + 1)]);
    expect(events[0].data).toEqual({ label: "房屋" });
  });

  it("rejects malformed JSON", async () => {
    const bytes = new TextEncoder().encode("event: turn\ndata: {bad}\n\n");
    await expect(collect([bytes])).rejects.toMatchObject({ code: "INVALID_SSE_JSON" });
  });
});

