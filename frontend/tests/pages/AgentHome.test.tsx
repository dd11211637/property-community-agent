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

function sseTurn(data: unknown) {
  return new Response(`event: run_started\ndata: {}\n\nevent: turn\ndata: ${JSON.stringify(data)}\n\nevent: done\ndata: {"done":true}\n\n`, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function mockAgentApi(...turns: unknown[]) {
  let turnIndex = 0;
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.endsWith("/api/agent/conversations")) {
      return Promise.resolve(envelope([]));
    }
    if (method === "GET" && url.endsWith("/api/agent/memories")) {
      return Promise.resolve(envelope([]));
    }
    const turn = turns[turnIndex++];
    if (turn === undefined) throw new Error(`Unexpected Agent request: ${method} ${url}`);
    return Promise.resolve(url.endsWith("/messages/stream") ? sseTurn(turn) : envelope(turn));
  });
}

function postedBodies(fetchMock: ReturnType<typeof vi.spyOn>) {
  return fetchMock.mock.calls
    .filter((call) => call[1]?.method === "POST")
    .map((call) => JSON.parse(String(call[1]?.body)));
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("Agent home flow", () => {
  it("recovers an archived conversation into a fresh V2 conversation", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    sessionStorage.setItem("property_agent_conversation_id", "retired-v1");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/api/agent/conversations")) return Promise.resolve(envelope([]));
      if (url.endsWith("/api/agent/memories")) return Promise.resolve(envelope([]));
      return Promise.resolve(new Response(JSON.stringify({
        success: false,
        data: null,
        error: { code: "CONVERSATION_CLOSED", message: "会话已结束" },
        request_id: "req-upgrade",
      }), { status: 409, headers: { "Content-Type": "application/json" } }));
    });

    render(<MemoryRouter><HomePage /></MemoryRouter>);

    expect(await screen.findByText(/会话已升级，请开始新对话/)).toBeInTheDocument();
    expect(sessionStorage.getItem("property_agent_conversation_id")).toBeNull();
  });

  it("guides an incomplete repair one question at a time", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    const fetchMock = mockAgentApi(
      {
        reply: "请描述一下具体出现了什么故障？",
        slot_prompt: {
          field: "description", label: "故障现象", prompt: "请描述一下具体出现了什么故障？",
          allow_custom: true, step: 1, total_steps: 3, completed: [], options: [],
        },
      },
      {
        reply: "这个故障发生在哪里？",
        slot_prompt: {
          field: "location", label: "发生地点", prompt: "这个故障发生在哪里？",
          allow_custom: true, step: 2, total_steps: 3,
          completed: [{ field: "description", label: "故障现象", value: "插座频繁跳闸" }],
          options: [{ label: "阳台", value: "阳台" }],
        },
      },
      {
        reply: "请选择预约上门时间",
        slot_prompt: {
          field: "appointment_at", label: "预约上门时间", prompt: "请选择预约上门时间",
          allow_custom: true, step: 3, total_steps: 3,
          completed: [
            { field: "description", label: "故障现象", value: "插座频繁跳闸" },
            { field: "location", label: "发生地点", value: "阳台" },
          ],
          options: [],
        },
      },
      {
        reply: "",
        pending_confirmation: {
          summary: "确认提交这条报修吗？",
          tool: "repair_create",
          params: {
            category: "ELECTRICAL", location: "阳台", description: "跳闸停电",
            appointment_at: "2026-08-31T13:25:00.000Z",
          },
          action_hash: "guided-hash",
        },
      },
    );

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), { target: { value: "我要报修" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "插座频繁跳闸" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("信息补充 2/3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "阳台" }));

    const appointmentInput = await screen.findByLabelText("发送给社区智能体");
    expect(appointmentInput).toHaveAttribute("type", "datetime-local");
    fireEvent.change(appointmentInput, { target: { value: "2026-08-31T21:25" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("确认提交这条报修吗？")).toBeInTheDocument();
    expect(screen.getByText("预约上门时间").closest("div")).toHaveTextContent(/2026.*21:25/);
    const bodies = postedBodies(fetchMock);
    expect(bodies[1].slots).toEqual({ description: "插座频繁跳闸" });
    expect(bodies[2].slots).toEqual({ location: "阳台" });
    expect(bodies[3].slots).toEqual({
      appointment_at: new Date("2026-08-31T21:25").toISOString(),
    });
  });

  it("guides a new announcement through title, body, and audience", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    const fetchMock = mockAgentApi(
      {
        reply: "请填写公告标题",
        slot_prompt: {
          field: "title", label: "公告标题", prompt: "请填写公告标题",
          allow_custom: true, step: 1, total_steps: 3, completed: [], options: [],
        },
      },
      {
        reply: "请填写公告正文",
        slot_prompt: {
          field: "body", label: "公告正文", prompt: "请填写公告正文",
          allow_custom: true, step: 2, total_steps: 3,
          completed: [{ field: "title", label: "公告标题", value: "1栋临时停水通知" }],
          options: [],
        },
      },
      {
        reply: "请选择公告受众，也可以输入具体楼栋",
        slot_prompt: {
          field: "audience", label: "受众范围", prompt: "请选择公告受众，也可以输入具体楼栋",
          allow_custom: true, step: 3, total_steps: 3,
          completed: [
            { field: "title", label: "公告标题", value: "1栋临时停水通知" },
            { field: "body", label: "公告正文", value: "今晚 22:00 至次日 06:00 停水" },
          ],
          options: [{ label: "全社区", value: {} }],
        },
      },
      {
        reply: "",
        pending_confirmation: {
          summary: "确认采用这份 AI 稿件并保存为公告草稿吗？",
          tool: "announcement_create_draft",
          params: { title: "1栋临时停水通知", body: "今晚停水", audience: {} },
          action_hash: "announcement-hash",
        },
      },
    );

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), { target: { value: "我要发布公告" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "1栋临时停水通知" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "今晚 22:00 至次日 06:00 停水" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(await screen.findByRole("button", { name: "全社区" }));

    expect(await screen.findByText("确认采用这份 AI 稿件并保存为公告草稿吗？")).toBeInTheDocument();
    const bodies = postedBodies(fetchMock);
    expect(bodies[1].slots).toEqual({ title: "1栋临时停水通知" });
    expect(bodies[2].slots).toEqual({ body: "今晚 22:00 至次日 06:00 停水" });
    expect(bodies[3].slots).toEqual({ audience: {} });
  });

  it("guides inspection creation through business fields instead of internal IDs", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    const fetchMock = mockAgentApi(
      { reply: "请填写巡检任务标题", slot_prompt: {
        field: "title", label: "任务标题", prompt: "请填写巡检任务标题",
        allow_custom: true, step: 1, total_steps: 3, completed: [], options: [],
      } },
      { reply: "请说明本次巡检要求", slot_prompt: {
        field: "description", label: "巡检要求", prompt: "请说明本次巡检要求",
        allow_custom: true, step: 2, total_steps: 3,
        completed: [{ field: "title", label: "任务标题", value: "每周小区安防巡检" }], options: [],
      } },
      { reply: "请选择巡检点位", slot_prompt: {
        field: "point", label: "巡检点位", prompt: "请选择巡检点位",
        allow_custom: true, step: 3, total_steps: 3,
        completed: [
          { field: "title", label: "任务标题", value: "每周小区安防巡检" },
          { field: "description", label: "巡检要求", value: "检查消防设施和通道" },
        ], options: [{ label: "消防通道", value: "消防通道" }],
      } },
      { reply: "", pending_confirmation: {
        summary: "确认创建这项巡检任务吗？", tool: "inspection_create",
        params: { title: "每周小区安防巡检", point: "消防通道" }, action_hash: "inspection-hash",
      } },
    );

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), { target: { value: "发起巡检" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "每周小区安防巡检" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(await screen.findByLabelText("发送给社区智能体"), { target: { value: "检查消防设施和通道" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(await screen.findByRole("button", { name: "消防通道" }));

    expect(await screen.findByText("确认创建这项巡检任务吗？")).toBeInTheDocument();
    const bodies = postedBodies(fetchMock);
    expect(bodies[1].slots).toEqual({ title: "每周小区安防巡检" });
    expect(bodies[2].slots).toEqual({ description: "检查消防设施和通道" });
    expect(bodies[3].slots).toEqual({ point: "消防通道" });
  });

  it("sends the selected house and renders the confirmed business result", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    sessionStorage.setItem("property_agent_house_id", "house-101");
    const fetchMock = mockAgentApi(
      {
        reply: "请确认报修",
        pending_confirmation: {
          summary: "确认创建报修",
          params: { location: "厨房" },
          action_hash: "hash-1",
        },
      },
      { reply: "报修工单已创建" },
    );

    render(<MemoryRouter><HomePage /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("发送给社区智能体"), {
      target: { value: "厨房漏水需要报修" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("确认创建报修")).toBeInTheDocument();
    const firstBody = postedBodies(fetchMock)[0];
    expect(firstBody.house_id).toBe("house-101");

    fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
    expect(await screen.findByText("报修工单已创建")).toBeInTheDocument();
    await waitFor(() => {
      const secondBody = postedBodies(fetchMock)[1];
      expect(secondBody).toEqual({ confirmed: true, action_hash: "hash-1" });
    });
  });

  it("renders inspection facts by entity_type without mislabeling them as announcements", async () => {
    sessionStorage.setItem("property_agent_token", "token");
    mockAgentApi({
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
    });

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
