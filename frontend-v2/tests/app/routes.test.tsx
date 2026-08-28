import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppProviders, AppRoutes, type ApplicationServices } from "../../src/app/App";
import { SessionProvider } from "../../src/auth/SessionContext";
import { useSession } from "../../src/auth/useSession";
import { createInMemorySessionStore, type SessionState } from "../../src/auth/session";
import { scopeQueryKey } from "../../src/query/keys";
import { demoAuthentication } from "../../examples/demoAdapters";
import { demoModels } from "../../examples/demoData";

function session(roles: string[], actorId = "actor-1"): SessionState {
  return { status: "authenticated", actor: { id: actorId, displayName: "测试用户", roles, communityName: "桂语社区" }, houses: [{ id: "house-a", label: "A" }, { id: "house-b", label: "B" }], currentHouseId: "house-a" };
}

function renderRoute(path: string, state: SessionState = { status: "unauthenticated" }) {
  const services: ApplicationServices = { sessionStore: createInMemorySessionStore(state), authentication: demoAuthentication, showcaseModels: demoModels };
  return render(<MemoryRouter initialEntries={[path]}><AppProviders services={services}><AppRoutes /></AppProviders></MemoryRouter>);
}

describe("application routing", () => {
  it("redirects protected routes to login", () => {
    renderRoute("/repairs");
    expect(screen.getByRole("heading", { name: "进入产品预览" })).toBeVisible();
  });

  it("shows resident navigation without privileged routes", () => {
    renderRoute("/", session(["RESIDENT"]));
    expect(screen.getByRole("heading", { name: /生活里的小事/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: "运营" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "管理" })).not.toBeInTheDocument();
  });

  it("renders operations and admin for explicit manager capability", () => {
    renderRoute("/admin", session(["MANAGER"]));
    expect(screen.getByRole("heading", { name: "管理与服务状态" })).toBeVisible();
    expect(screen.getByRole("link", { name: "运营" })).toBeVisible();
  });

  it("shows forbidden for resident operations access", () => {
    renderRoute("/operations", session(["RESIDENT"]));
    expect(screen.getByRole("heading", { name: "当前身份无权访问" })).toBeVisible();
  });

  it("renders a real not-found page", () => {
    renderRoute("/does-not-exist");
    expect(screen.getByRole("heading", { name: "这里还没有社区服务" })).toBeVisible();
  });
});

function ScopedResource() {
  const { session, selectHouse } = useSession();
  const authenticated = session.status === "authenticated";
  const key = scopeQueryKey({ actorId: authenticated ? session.actor.id : "anonymous", houseId: authenticated ? session.currentHouseId : null }, "resource");
  const query = useQuery({ queryKey: key, queryFn: async () => new Promise<string>(() => undefined), enabled: authenticated });
  if (!authenticated) return null;
  return <><span>{query.data ?? "新房屋加载中"}</span><button onClick={() => void selectHouse("house-b")}>切换</button></>;
}

describe("house transition", () => {
  it("never presents cached House A data as House B data", async () => {
    const user = userEvent.setup();
    const store = createInMemorySessionStore(session(["RESIDENT"]));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(scopeQueryKey({ actorId: "actor-1", houseId: "house-a" }, "resource"), "House A 私有内容");
    render(<QueryClientProvider client={client}><SessionProvider store={store} auth={demoAuthentication}><ScopedResource /></SessionProvider></QueryClientProvider>);
    expect(screen.getByText("House A 私有内容")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "切换" }));
    await waitFor(() => expect(screen.getByText("新房屋加载中")).toBeVisible());
    expect(screen.queryByText("House A 私有内容")).not.toBeInTheDocument();
  });
});
