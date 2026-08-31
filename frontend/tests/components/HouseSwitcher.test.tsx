import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Session } from "../../src/api/contracts";
import { AuthProvider } from "../../src/auth/AuthContext";
import { AppShell } from "../../src/components/AppShell";
import { HouseSwitcher } from "../../src/components/HouseSwitcher";

function renderSwitcher(session: Session) {
  sessionStorage.setItem("property_agent_session", JSON.stringify(session));
  sessionStorage.setItem("property_agent_token", session.access_token);
  sessionStorage.setItem("property_agent_house_id", session.current_house_id ?? "");
  render(<AuthProvider><HouseSwitcher workspace="resident" /></AuthProvider>);
}

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("HouseSwitcher", () => {
  it("blocks house-scoped pages until a multi-house resident selects a house", () => {
    const session: Session = {
      access_token: "token",
      actor: { id: "resident", display_name: "李四", roles: ["RESIDENT"], community_name: "幸福小区" },
      houses: [{ id: "house-1", label: "绑定房屋 1" }, { id: "house-2", label: "绑定房屋 2" }],
      current_house_id: null,
    };
    sessionStorage.setItem("property_agent_session", JSON.stringify(session));
    sessionStorage.setItem("property_agent_token", session.access_token);

    render(<AuthProvider><MemoryRouter><Routes>
      <Route element={<AppShell />}><Route index element={<span>房屋业务页</span>} /></Route>
    </Routes></MemoryRouter></AuthProvider>);

    expect(screen.getByRole("heading", { name: "请先选择要服务的房屋" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "当前房屋" })).toHaveFocus();
    expect(screen.queryByText("房屋业务页")).not.toBeInTheDocument();
  });

  it("presents one bound house as a readable non-interactive value", () => {
    renderSwitcher({
      access_token: "token",
      actor: { id: "resident", display_name: "张三", roles: ["RESIDENT"], community_name: "幸福小区" },
      houses: [{ id: "house-1", label: "1栋 1单元 101" }],
      current_house_id: "house-1",
    });
    expect(screen.getByLabelText("当前房屋")).toHaveTextContent("1栋 1单元 101");
    expect(screen.queryByRole("combobox", { name: "当前房屋" })).not.toBeInTheDocument();
  });

  it("hydrates a readable address for a restored legacy session", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      house_id: "house-1", building: "1栋", unit: "1单元", room_no: "101",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderSwitcher({
      access_token: "token",
      actor: { id: "resident", display_name: "张三", roles: ["RESIDENT"], community_name: "幸福小区" },
      houses: [{ id: "house-1", label: "当前房屋" }],
      current_house_id: "house-1",
    });
    await waitFor(() => expect(screen.getByLabelText("当前房屋")).toHaveTextContent("1栋 1单元 101"));
  });

  it("keeps the previous house selected and explains a failed switch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "房屋切换失败，请稍后重试。" }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    ));
    renderSwitcher({
      access_token: "token",
      actor: { id: "resident", display_name: "李四", roles: ["RESIDENT"], community_name: "幸福小区" },
      houses: [{ id: "house-1", label: "1栋 1单元 101" }, { id: "house-2", label: "绑定房屋 2" }],
      current_house_id: "house-1",
    });
    const picker = screen.getByRole("combobox", { name: "当前房屋" });
    await userEvent.setup().selectOptions(picker, "house-2");
    expect(await screen.findByRole("alert")).toHaveTextContent("房屋切换失败，请稍后重试。");
    expect(picker).toHaveValue("house-1");
  });
});
