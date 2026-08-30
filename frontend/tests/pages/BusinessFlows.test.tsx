import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Role, Session } from "../../src/api/contracts";
import { AuthProvider } from "../../src/auth/AuthContext";
import { AnnouncementsPage } from "../../src/pages/AnnouncementsPage";
import { BillingPage } from "../../src/pages/BillingPage";
import { InspectionPage } from "../../src/pages/InspectionPage";
import { RepairsPage } from "../../src/pages/RepairsPage";

function envelope(data: unknown) {
  return new Response(JSON.stringify({ success: true, data, error: null, request_id: "req-m3" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAs(page: React.ReactNode, roles: Role[], houseId = "house-1") {
  const session: Session = {
    access_token: "token",
    actor: { id: "actor-1", display_name: "测试用户", roles, community_name: "测试社区" },
    houses: [{ id: houseId, label: "1栋 101" }],
    current_house_id: houseId,
  };
  sessionStorage.setItem("property_agent_session", JSON.stringify(session));
  sessionStorage.setItem("property_agent_token", session.access_token);
  render(<AuthProvider>{page}</AuthProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("M3 business pages", () => {
  it("executes a repair action with the backend version and idempotency key", async () => {
    const workOrder = {
      id: "work-1", business_no: "WO-001", house_id: "house-1", category: "OTHER",
      location: "客厅", description: "灯具损坏", urgency: "NORMAL", status: "ASSIGNED",
      version: 3, assignee_id: "worker-1", available_actions: ["ACCEPT"],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/work-orders") return envelope({ items: [workOrder], total: 1, limit: 20, offset: 0 });
      if (url === "/api/work-orders/work-1/timeline") return envelope([]);
      if (url === "/api/work-orders/work-1/actions/accept" && init?.method === "POST") return envelope({ ...workOrder, status: "ACCEPTED", version: 4 });
      if (url === "/api/work-orders/work-1") return envelope(workOrder);
      throw new Error(`unexpected request: ${url}`);
    });

    renderAs(<RepairsPage />, ["REPAIR_WORKER"]);
    fireEvent.click(await screen.findByText("灯具损坏"));
    fireEvent.click(await screen.findByRole("button", { name: "接单" }));
    fireEvent.click(screen.getByRole("button", { name: "确认操作" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => {
      if (url !== "/api/work-orders/work-1/actions/accept" || init?.method !== "POST") return false;
      const headers = new Headers(init.headers);
      return headers.get("Idempotency-Key")?.startsWith("repair-accept_")
        && JSON.parse(String(init.body)).expected_version === 3;
    })).toBe(true));
  });

  it("shows only the resident announcement view without staff controls", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(envelope({
      items: [{ id: "announcement-1", business_no: "AN-001", title: "停水通知", body: "明日停水", category: "MAINTENANCE", audience_condition: {}, status: "PUBLISHED", version: 5, published_at: "2026-08-09T00:00:00Z", available_actions: [] }],
      total: 1, limit: 20, offset: 0,
    }));

    renderAs(<AnnouncementsPage />, ["RESIDENT"]);
    expect(await screen.findByText("停水通知")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /新建草稿/ })).not.toBeInTheDocument();
  });

  it("loads announcement version history for authorized staff", async () => {
    const announcement = {
      id: "announcement-1", business_no: "AN-001", title: "停水通知", body: "明日停水",
      category: "MAINTENANCE", audience_condition: {}, status: "DRAFT", version: 2,
      available_actions: ["EDIT", "SUBMIT_REVIEW"],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/announcements") return envelope({ items: [announcement], limit: 50, offset: 0 });
      if (url === "/api/announcements/announcement-1") return envelope(announcement);
      if (url === "/api/announcements/announcement-1/versions") return envelope([
        {
          version_no: 1, title: "停水通知（初稿）", body: "初稿正文", category: "MAINTENANCE",
          audience_condition: {}, operator_id: "actor-1", source: "MANUAL",
          created_at: "2026-08-09T00:00:00Z",
        },
        {
          version_no: 2, title: "停水通知", body: "明日停水", category: "MAINTENANCE",
          audience_condition: {}, operator_id: "actor-1", source: "MANUAL",
          created_at: "2026-08-10T00:00:00Z",
        },
      ]);
      throw new Error(`unexpected request: ${url}`);
    });

    renderAs(<AnnouncementsPage />, ["CUSTOMER_SERVICE"]);
    fireEvent.click(await screen.findByText("停水通知"));
    fireEvent.click(await screen.findByRole("button", { name: "查看版本历史" }));

    expect(await screen.findByRole("region", { name: "版本历史" })).toBeInTheDocument();
    expect(screen.getByText("v1 · 停水通知（初稿）")).toBeInTheDocument();
    expect(screen.getByText("v2 · 停水通知")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/announcements/announcement-1/versions",
      expect.any(Object),
    );
  });

  it("renders bill breakdown and an explicit missing-rule warning", async () => {
    const bill = { bill_id: "bill-1", bill_period: "2026-08", total_amount: "128.00", status: "UNPAID", version: 1, property_fee: "100.00", utility_fee: "28.00", parking_fee: "0.00", late_fee: "0.00" };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/billing/bills") return envelope([bill]);
      if (url === "/api/billing/consultations") return envelope([]);
      if (url === "/api/billing/bills/bill-1") return envelope({ bill, rule: null, unknown_rule: true, consultation_entry: null });
      throw new Error(`unexpected request: ${url}`);
    });

    renderAs(<BillingPage />, ["RESIDENT"]);
    fireEvent.click(await screen.findByRole("button", { name: /2026-08/ }));
    expect(await screen.findByText("费用依据待核实")).toBeInTheDocument();
    expect(screen.getByText("物业费")).toBeInTheDocument();
  });

  it("renders inspection and security actions supplied by the backend", async () => {
    const task = { id: "task-1", business_no: "IT-001", title: "夜间巡检", description: "检查消防通道", status: "ASSIGNED", version: 2, route_points: ["A栋"], assignee_id: "guard-1", available_actions: ["START"] };
    const securityEvent = { id: "event-1", business_no: "SE-001", event_type: "FIRE", risk_level: "HIGH_RISK", location: "A栋", description: "烟雾告警", status: "PENDING", version: 1, available_actions: ["GRADE_CONFIRM"] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/inspection-tasks") return envelope({ items: [task], total: 1, limit: 20, offset: 0 });
      if (url === "/api/security-events") return envelope({ items: [securityEvent], total: 1, limit: 20, offset: 0 });
      if (url === "/api/inspection-tasks/task-1") return envelope(task);
      if (url === "/api/inspection-tasks/task-1/timeline") return envelope([]);
      throw new Error(`unexpected request: ${url}`);
    });

    renderAs(<InspectionPage />, ["SECURITY_GUARD"]);
    fireEvent.click(await screen.findByText("夜间巡检"));
    expect(await screen.findByRole("button", { name: "开始巡检" })).toBeInTheDocument();
    expect(screen.getByText("烟雾告警")).toBeInTheDocument();
  });
});
