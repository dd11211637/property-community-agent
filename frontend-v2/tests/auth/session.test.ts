import { describe, expect, it, vi } from "vitest";
import { createInMemorySessionStore } from "../../src/auth/session";

describe("in-memory SessionStore", () => {
  it("publishes state without touching browser persistence", () => {
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    const sessionSpy = vi.spyOn(window.sessionStorage, "setItem");
    const store = createInMemorySessionStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.setState({ status: "authenticated", actor: { id: "a", displayName: "A", roles: ["RESIDENT"], communityName: "C" }, houses: [], currentHouseId: null });
    expect(store.getSnapshot().status).toBe("authenticated");
    expect(listener).toHaveBeenCalledOnce();
    expect(localSpy).not.toHaveBeenCalled();
    expect(sessionSpy).not.toHaveBeenCalled();
    unsubscribe();
    localSpy.mockRestore();
    sessionSpy.mockRestore();
  });
});
