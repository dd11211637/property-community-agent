import { afterEach, describe, expect, it, vi } from "vitest";
import { createUuid } from "../../src/platform/uuid";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createUuid", () => {
  it("creates an RFC 4122 version 4 UUID without crypto.randomUUID", () => {
    vi.stubGlobal("crypto", {
      getRandomValues(bytes: Uint8Array) {
        bytes.set(Array.from({ length: 16 }, (_, index) => index));
        return bytes;
      },
    });

    expect(createUuid()).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
  });
});
