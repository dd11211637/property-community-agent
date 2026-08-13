import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "../../src/pages/HomePage";

function envelope(data: unknown) {
  return new Response(JSON.stringify({ success: true, data, error: null, request_id: "req-agent" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("Agent home flow", () => {
  it("guides an incomplete repair one question at a time", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(envelope({
        reply: "请描述一下具体出现了什么故障？",
        slot_prompt: {
          field: "description", label: "故障现象", prompt: "请描述一下具体出现了什么故障？",
          allow_custom: true, step: 1, total_steps: 2, completed: [], options: [],
        },
      }))
      .mockResolvedValueOnce(envelope({
        reply: "这个故障发生在哪里？",
        slot_prompt: {
          field: "location", label: "发生地点", prompt: "这个故障发生在哪里？",
          allow_custom: true, step: 2, total_steps: 2,
          completed: [{ field: "description", label: "故障现象", value: "插座频繁跳闸" }],
          options: [{ label: "阳台", value: "阳台" }],
        },
      }))
      .mockResolvedValueOnce(envelope({
        reply: "",
        pending_confirmation: {
          summary: "确认提交这条报修吗？",
          tool: "repair_create",
          params: { category: "ELECTRICAL", location: "阳台", description: "跳闸停电" },
          action_hash: "guided-hash",
        },
      }));

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), { target: { value: "我要报修" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "插座频繁跳闸" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("信息补充 2/2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "阳台" }));

    expect(await screen.findByText("确认提交这条报修吗？")).toBeInTheDocument();
    const bodies = fetchMock.mock.calls.map((call) => JSON.parse(String(call[1]?.body)));
    expect(bodies[1].slots).toEqual({ description: "插座频繁跳闸" });
    expect(bodies[2].slots).toEqual({ location: "阳台" });
  });

  it("sends the selected house and renders the confirmed business result", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    sessionStorage.setItem("property_agent_house_id", "house-101");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(envelope({
        reply: "请确认报修",
        pending_confirmation: {
          summary: "确认创建报修",
          params: { location: "厨房" },
          action_hash: "hash-1",
        },
      }))
      .mockResolvedValueOnce(envelope({ reply: "报修工单已创建" }));

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), {
      target: { value: "厨房漏水需要报修" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("确认创建报修")).toBeInTheDocument();
    const firstBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(firstBody.house_id).toBe("house-101");

    fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
    expect(await screen.findByText("报修工单已创建")).toBeInTheDocument();
    await waitFor(() => {
      const secondBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
      expect(secondBody).toEqual({ confirmed: true, action_hash: "hash-1" });
    });
  });

  it("renders inspection facts by entity_type without mislabeling them as announcements", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(envelope({
      reply: "当前共有 2 项巡检任务，已完成 1 项，还有 1 项未完成。",
      facts: {
        total: 2,
        completed: 1,
        incomplete: 1,
        status_counts: { COMPLETED: 1, ASSIGNED: 1 },
        items: [
          {
            entity_type: "INSPECTION_TASK",
            id: "task-1",
            business_no: "IT-001",
            title: "消防通道巡检",
            status: "ASSIGNED",
            route_points: ["1栋大厅", "消防通道"],
          },
          {
            entity_type: "SECURITY_EVENT",
            id: "event-1",
            business_no: "AQ-001",
            event_type: "EQUIPMENT_FAULT",
            risk_level: "MEDIUM",
            location: "地下车库",
            description: "照明故障",
            status: "REPORTED",
          },
        ],
      },
    }));

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), {
      target: { value: "巡检任务都完成了吗" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("巡检完成情况")).toBeInTheDocument();
    expect(screen.getByText("消防通道巡检")).toBeInTheDocument();
    expect(screen.getByText("地下车库")).toBeInTheDocument();
    expect(screen.getByText("1 项未完成")).toBeInTheDocument();
    expect(screen.queryByText("已发布公告")).not.toBeInTheDocument();
  });
});
