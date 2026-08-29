import { describe, expect, it, vi } from "vitest";
import { browserSessionKey, createBrowserSessionStore, createInMemorySessionStore, type AuthenticatedSession } from "../../src/auth/session";

function session(overrides: Partial<AuthenticatedSession> = {}): AuthenticatedSession {
  return { status: "authenticated", accessToken: "token", actor: { id: "actor-a", displayName: "A", communityId: "community-a", communityName: "社区", roles: ["RESIDENT"] }, houses: [{ id: "house-a", label: "房屋 · house-a", resolved: false }], currentHouseId: "house-a", ...overrides };
}

describe("SessionStore", () => {
  it("keeps in-memory adapters away from browser persistence", () => {
    const spy = vi.spyOn(window.sessionStorage, "setItem");
    const store = createInMemorySessionStore();
    store.setState(session());
    expect(store.getSnapshot()).toEqual(session());
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("restores one versioned record and logout removes it", () => {
    const store = createBrowserSessionStore(window.sessionStorage);
    expect(store.getSnapshot().status).toBe("restoring");
    store.restore();
    store.setState(session());
    expect(Object.keys(window.sessionStorage)).toEqual([browserSessionKey]);
    const restored = createBrowserSessionStore(window.sessionStorage);
    restored.restore();
    expect(restored.getSnapshot()).toEqual(session());
    restored.clear();
    expect(window.sessionStorage.getItem(browserSessionKey)).toBeNull();
  });

  it.each([
    { version: 2, session: session() },
    { version: 1, session: { ...session(), actor: { ...session().actor, roles: "RESIDENT" } } },
    { version: 1, session: { ...session(), currentHouseId: "not-bound" } },
    { version: 1, session: { ...session(), houses: [{ id: "house-a", label: "fake", resolved: true }] } },
  ])("fails closed and deletes malformed or unknown records", (record) => {
    window.sessionStorage.setItem(browserSessionKey, JSON.stringify(record));
    const store = createBrowserSessionStore(window.sessionStorage);
    store.restore();
    expect(store.getSnapshot()).toEqual({ status: "unauthenticated" });
    expect(window.sessionStorage.getItem(browserSessionKey)).toBeNull();
  });
});
