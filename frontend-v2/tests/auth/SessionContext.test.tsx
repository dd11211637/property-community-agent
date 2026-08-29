import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../src/api/client";
import { SessionProvider } from "../../src/auth/SessionContext";
import { useSession } from "../../src/auth/useSession";
import { createInMemorySessionStore, type AuthenticatedSession, type AuthenticationPort } from "../../src/auth/session";
import { scopeQueryKey } from "../../src/query/keys";

function session(actorId = "actor-a", houses = ["house-a", "house-b"], currentHouseId: string | null = "house-a"): AuthenticatedSession {
  return { status: "authenticated", accessToken: `token-${actorId}`, actor: { id: actorId, displayName: actorId, communityId: "community", communityName: "社区", roles: ["RESIDENT"] }, houses: houses.map((id) => ({ id, label: `房屋 · ${id}`, resolved: false })), currentHouseId };
}

function Harness() {
  const value = useSession();
  return <div><span data-testid="status">{value.session.status}</span><span data-testid="house">{value.session.status === "authenticated" ? value.session.currentHouseId ?? "none" : "none"}</span><span data-testid="error">{value.selectionError}</span><button onClick={() => void value.selectHouse("house-b")}>选择 B</button><button onClick={() => void value.signIn({ username: "b", password: "p" })}>登录 B</button><button onClick={() => void value.signOut()}>退出</button></div>;
}

function renderProvider(store: ReturnType<typeof createInMemorySessionStore>, auth: AuthenticationPort, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(<QueryClientProvider client={client}><SessionProvider store={store} auth={auth}><Harness /></SessionProvider></QueryClientProvider>);
  return client;
}

describe("SessionProvider scope transitions", () => {
  it("resolves a server-selected single house after establishing login scope", async () => {
    const auth: AuthenticationPort = { signIn: vi.fn(async () => session("actor-a", ["house-a"], "house-a")), selectHouse: vi.fn(async () => ({ houseId: "house-a", building: "1 栋", unit: "2 单元", roomNo: "1203" })) };
    const store = createInMemorySessionStore();
    renderProvider(store, auth);
    await userEvent.click(screen.getByRole("button", { name: "登录 B" }));
    await waitFor(() => expect(store.getSnapshot()).toMatchObject({ status: "authenticated", currentHouseId: "house-a", houses: [{ resolved: true, label: "1 栋 · 2 单元 · 1203" }] }));
  });

  it("commits House B only after validation and removes old-scope cache", async () => {
    let resolveSelection!: (value: { houseId: string; building: string; unit: string; roomNo: string }) => void;
    const selection = new Promise<{ houseId: string; building: string; unit: string; roomNo: string }>((resolve) => { resolveSelection = resolve; });
    const auth: AuthenticationPort = { signIn: vi.fn(), selectHouse: vi.fn(() => selection) };
    const store = createInMemorySessionStore(session());
    const client = renderProvider(store, auth);
    client.setQueryData(scopeQueryKey({ actorId: "actor-a", houseId: "house-a" }, "private"), "A data");
    await userEvent.click(screen.getByRole("button", { name: "选择 B" }));
    expect(store.getSnapshot()).toMatchObject({ currentHouseId: "house-a" });
    resolveSelection({ houseId: "house-b", building: "2 栋", unit: "1 单元", roomNo: "802" });
    await waitFor(() => expect(store.getSnapshot()).toMatchObject({ currentHouseId: "house-b" }));
    expect(client.getQueryData(scopeQueryKey({ actorId: "actor-a", houseId: "house-a" }, "private"))).toBeUndefined();
    expect(store.getSnapshot()).toMatchObject({ houses: [{ id: "house-a" }, { id: "house-b", resolved: true, label: "2 栋 · 1 单元 · 802" }] });
  });

  it.each([["forbidden", 403], ["unknown", 404], ["network", 0]] as const)("retains the original house on %s selection failure", async (kind, status) => {
    const auth: AuthenticationPort = { signIn: vi.fn(), selectHouse: vi.fn(async () => { throw new ApiError(kind, status, "FAIL", "fail"); }) };
    const store = createInMemorySessionStore(session());
    renderProvider(store, auth);
    await userEvent.click(screen.getByRole("button", { name: "选择 B" }));
    await waitFor(() => expect(screen.getByTestId("error")).not.toHaveTextContent(""));
    expect(store.getSnapshot()).toMatchObject({ currentHouseId: "house-a" });
  });

  it("clears authenticated cache before Actor B commit and on logout", async () => {
    const auth: AuthenticationPort = { signIn: vi.fn(async () => session("actor-b", [], null)), selectHouse: vi.fn() };
    const store = createInMemorySessionStore(session("actor-a"));
    const client = renderProvider(store, auth);
    client.setQueryData(scopeQueryKey({ actorId: "actor-a", houseId: "house-a" }, "private"), "A data");
    await userEvent.click(screen.getByRole("button", { name: "登录 B" }));
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(store.getSnapshot()).toMatchObject({ actor: { id: "actor-b" } });
    client.setQueryData(scopeQueryKey({ actorId: "actor-b", houseId: null }, "private"), "B data");
    await userEvent.click(screen.getByRole("button", { name: "退出" }));
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(store.getSnapshot()).toEqual({ status: "unauthenticated" });
  });
});
