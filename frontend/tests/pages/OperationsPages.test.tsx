import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminPage } from "../../src/pages/AdminPage";
import { MessagesPage } from "../../src/pages/MessagesPage";

function envelope(data: unknown) {
  return new Response(JSON.stringify({ success: true, data, error: null, request_id: "req-test" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const unreadMessage = {
  id: "message-1",
  business_type: "REPAIR",
  resource_id: "repair-1",
  title: "报修已派单",
  body: "维修人员已接单",
  status: "SENT",
  is_read: false,
  read_at: null,
  retry_count: 0,
  max_retry_count: 5,
  retry_exhausted: false,
  handover_status: null,
  fallback_contact: null,
  created_at: "2026-08-09T00:00:00Z",
  updated_at: "2026-08-09T00:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("M2 operations pages", () => {
  it("loads real messages and sends an idempotent read-all request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(envelope({ items: [unreadMessage], total: 1, limit: 20, offset: 0 }))
      .mockResolvedValueOnce(envelope({ updated_count: 1, read_at: "2026-08-09T01:00:00Z" }))
      .mockResolvedValueOnce(envelope({ items: [], total: 0, limit: 20, offset: 0 }));

    render(<MessagesPage />);
    expect(await screen.findByText("报修已派单")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /全部标为已读/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/messages/read-all");
    const headers = new Headers(fetchMock.mock.calls[1][1]?.headers);
    expect(headers.get("Idempotency-Key")).toMatch(/^message-read-all_/);
  });

  it("renders dashboard aggregates instead of placeholders", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(envelope({
      pending_count: 2,
      failed_message_count: 1,
      high_risk_event_count: 3,
      pending_items: [],
      failed_messages: [{
        ...unreadMessage,
        id: "failed-message",
        title: "消息投递失败",
        status: "FAILED",
        retry_count: 5,
        retry_exhausted: true,
        handover_status: "PENDING",
        fallback_contact: "138****0002",
      }],
      high_risk_events: [],
      integration_health: { database: "UP", message_delivery: "DEGRADED" },
    }));

    render(<AdminPage />);
    expect(await screen.findByText("服务支撑状态")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("需要关注")).toBeInTheDocument();
    expect(screen.getByText("消息投递失败")).toBeInTheDocument();
    expect(screen.getByText(/138\*\*\*\*0002/)).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });
});
