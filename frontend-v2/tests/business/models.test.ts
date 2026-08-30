import { describe, expect, it } from "vitest";
import {
  describeAudience,
  parseAdminDashboard,
  parseAnnouncement,
  parseCollection,
  parseInspectionTask,
  parseMessage,
  parseSecurityEvent,
  parseWorkOrder,
} from "../../src/business/models";

describe("business response parsers", () => {
  it("maps a generic work-order Envelope payload into presentation truth", () => {
    const item = parseWorkOrder({
      id: "wo-1",
      business_no: "BX-001",
      house_id: "house-a",
      house_display: "1栋 1单元 101室",
      reporter_id: "resident-1",
      reporter_name: "张三",
      category: "ELECTRICAL",
      location: "厨房",
      description: "跳闸",
      urgency: "URGENT",
      status: "PENDING_ASSIGNMENT",
      version: 2,
      assignee_id: null,
      assignee_name: null,
      has_review: false,
      available_actions: ["ASSIGN"],
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T01:00:00Z",
    });
    expect(item).toMatchObject({
      id: "wo-1",
      number: "BX-001",
      version: 2,
      availableActions: ["ASSIGN"],
      reporterName: "张三",
      houseDisplay: "1栋 1单元 101室",
    });
  });

  it("fails closed for malformed required business fields", () => {
    expect(() => parseWorkOrder({ id: "wo-1" })).toThrow(/business_no/);
    expect(() =>
      parseCollection({ items: "not-an-array" }, parseWorkOrder, "workOrders"),
    ).toThrow(/必须是数组/);
  });

  it("filters Agent-only inspection actions", () => {
    const task = parseInspectionTask({
      id: "task-1",
      title: "夜巡",
      description: "",
      status: "SUBMITTED",
      version: 1,
      assignee_id: "guard-1",
      route_points: ["北门"],
      available_actions: ["COMPLETE", "CONFIRM_AI"],
      created_at: "",
      updated_at: "",
    });
    expect(task.availableActions).toEqual(["COMPLETE"]);
  });

  it("maps announcements, security, messages and admin health without raw JSON DTOs", () => {
    const announcement = parseAnnouncement({
      id: "a-1",
      title: "停水",
      body: "今晚停水",
      category: "MAINTENANCE",
      status: "PUBLISHED",
      version: 3,
      audience_condition: { buildings: ["1栋"] },
      available_actions: [],
      created_at: "",
      updated_at: "",
    });
    expect(describeAudience(announcement.audience)).toBe("楼栋：1栋");
    expect(
      parseSecurityEvent({
        id: "e-1",
        business_no: "AQ-1",
        event_type: "FIRE",
        risk_level: "HIGH_RISK",
        location: "车库",
        description: "烟雾",
        status: "REPORTED",
        version: 1,
        available_actions: [],
        created_at: "",
        updated_at: "",
      }).riskLevel,
    ).toBe("HIGH_RISK");
    expect(
      parseMessage({
        id: "m-1",
        title: "失败通知",
        body: "需要人工处理",
        status: "FAILED",
        retry_count: 3,
        handover_status: "PENDING",
        created_at: "",
      }).handoverRequired,
    ).toBe(true);
    const dashboard = parseAdminDashboard({
      pending_items: [{ id: "p-1" }],
      failed_messages: [],
      high_risk_events: [],
      integration_health: {
        database: "UP",
        model_gateway: "CONFIGURED_NOT_PROBED",
      },
      pending_count: 1,
    });
    expect(dashboard.pending).toHaveLength(1);
    expect(dashboard.integrationHealth).toContainEqual({
      name: "model_gateway",
      status: "CONFIGURED_NOT_PROBED",
    });
  });
});
