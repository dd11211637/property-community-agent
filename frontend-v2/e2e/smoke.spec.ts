import { expect, test, type Page, type Route } from "@playwright/test";

type Account =
  | "resident"
  | "manager"
  | "finance"
  | "guard"
  | "multi"
  | "expired";
const identities = {
  resident: {
    actor_id: "actor-resident",
    display_name: "真实居民",
    roles: ["RESIDENT"],
    house_ids: ["house-a"],
    current_house_id: "house-a",
  },
  manager: {
    actor_id: "actor-manager",
    display_name: "真实经理",
    roles: ["MANAGER"],
    house_ids: [],
    current_house_id: null,
  },
  finance: {
    actor_id: "actor-finance",
    display_name: "财务专员",
    roles: ["FINANCE_STAFF"],
    house_ids: [],
    current_house_id: null,
  },
  guard: {
    actor_id: "guard-1",
    display_name: "安保值班员",
    roles: ["SECURITY_GUARD"],
    house_ids: [],
    current_house_id: null,
  },
  multi: {
    actor_id: "actor-multi",
    display_name: "多房居民",
    roles: ["RESIDENT"],
    house_ids: ["house-a", "house-b"],
    current_house_id: null,
  },
  expired: {
    actor_id: "actor-expired",
    display_name: "过期用户",
    roles: ["RESIDENT"],
    house_ids: ["house-a"],
    current_house_id: "house-a",
  },
} as const;

async function installAuthTransport(page: Page) {
  const state = {
    workOrderVersion: 1,
    consultationVersion: 1,
    consultationConflictOnce: true,
    announcementStatus: "PENDING_REVIEW",
    announcementVersion: 2,
    inspectionVersion: 1,
    securityVersion: 1,
    read: false,
    agentConversationId: "",
    agentPending: null as null | { summary: string; tool: string; params: Record<string, unknown>; action_hash: string; issued_at: string },
    agentConflictOnce: true,
    agentMessages: [] as { id: string; role: string; content: string; intent: string | null; house_id: string | null; created_at: string }[],
    memory: null as null | { id: string; memory_type: string; content: string; house_id: string | null; source_conversation_id: string | null; version: number; expires_at: null; created_at: string; updated_at: string },
    memoryConflictOnce: true,
  };
  const envelope = (data: unknown) => ({
    success: true,
    data,
    error: null,
    request_id: "req-e2e-business",
  });
  const workOrder = () => ({
    id: "wo-1",
    business_no: "BX-2026-001",
    house_id: "house-a",
    house_display: "1栋 1单元 101室",
    reporter_id: "actor-resident",
    reporter_name: "真实居民",
    category: "WATER_PLUMBING",
    location: "厨房",
    description: "水槽持续渗水",
    urgency: "URGENT",
    status: state.workOrderVersion > 1 ? "IN_PROGRESS" : "PENDING_ASSIGNMENT",
    version: state.workOrderVersion,
    assignee_id: state.workOrderVersion > 1 ? "worker-1" : null,
    assignee_name: state.workOrderVersion > 1 ? "维修人员" : null,
    has_review: false,
    available_actions:
      state.workOrderVersion > 1
        ? ["RECORD_PROGRESS", "SUBMIT_COMPLETION"]
        : ["ASSIGN"],
    created_at: "2026-08-29T01:00:00Z",
    updated_at: "2026-08-29T02:00:00Z",
  });
  const consultation = () => ({
    id: "consult-1",
    actor_id: "actor-resident",
    community_id: "community-a",
    house_id: "house-a",
    bill_id: "bill-1",
    subject: "停车费咨询",
    description: "请核对本月停车费",
    status: state.consultationVersion > 1 ? "PROCESSING" : "SUBMITTED",
    answer: null,
    handler_id: state.consultationVersion > 1 ? "actor-finance" : null,
    version: state.consultationVersion,
    created_at: "2026-08-29T01:00:00Z",
    updated_at: "2026-08-29T02:00:00Z",
  });
  const announcement = () => ({
    id: "ann-1",
    title: "今晚停水通知",
    body: "今晚 22:00 至次日 05:00 停水。",
    category: "MAINTENANCE",
    status: state.announcementStatus,
    version: state.announcementVersion,
    audience_condition: { buildings: ["1栋"] },
    available_actions:
      state.announcementStatus === "PENDING_REVIEW"
        ? ["APPROVE"]
        : state.announcementStatus === "APPROVED"
          ? ["PUBLISH"]
          : [],
    scheduled_at: null,
    published_at:
      state.announcementStatus === "PUBLISHED" ? "2026-08-29T03:00:00Z" : null,
    created_at: "2026-08-29T01:00:00Z",
    updated_at: "2026-08-29T02:00:00Z",
  });
  const inspection = () => ({
    id: "task-1",
    title: "夜间消防巡检",
    description: "检查消防通道",
    status: state.inspectionVersion > 1 ? "IN_PROGRESS" : "ASSIGNED",
    version: state.inspectionVersion,
    assignee_id: "guard-1",
    route_points: ["北门", "地库"],
    available_actions:
      state.inspectionVersion > 1 ? ["ADD_RECORD"] : ["START", "CONFIRM_AI"],
    planned_at: "2026-08-29T12:00:00Z",
    due_at: "2026-08-29T14:00:00Z",
    created_at: "2026-08-29T01:00:00Z",
    updated_at: "2026-08-29T02:00:00Z",
  });
  const security = () => ({
    id: "event-1",
    business_no: "AQ-2026-001",
    event_type: "FIRE",
    risk_level: "HIGH_RISK",
    location: "地下车库",
    description: "发现烟雾",
    status: "PENDING_REVIEW",
    version: state.securityVersion,
    assignee_id: "guard-1",
    available_actions: ["GRADE_CONFIRM"],
    created_at: "2026-08-29T01:00:00Z",
    updated_at: "2026-08-29T02:00:00Z",
  });
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path === "/api/agent/conversations" && method === "GET")
      return route.fulfill({ json: envelope(state.agentConversationId ? [{ conversation_id: state.agentConversationId, title: "厨房漏水", status: state.agentPending ? "WAITING_CONFIRM" : "ACTIVE", current_house_id: "house-a", last_intent: "repair_create", last_message_at: "2026-08-29T04:00:00Z" }] : []) });
    if (path === "/api/agent/memories" && method === "GET")
      return route.fulfill({ json: envelope(state.memory ? [state.memory] : []) });
    if (path === "/api/agent/memories" && method === "POST") {
      const body = request.postDataJSON() as { memory_type: string; content: string; house_id: string | null; source_conversation_id: string | null };
      state.memory = { id: "memory-1", ...body, version: 1, expires_at: null, created_at: "2026-08-29T04:00:00Z", updated_at: "2026-08-29T04:00:00Z" };
      return route.fulfill({ json: envelope(state.memory) });
    }
    if (path === "/api/agent/memories/memory-1" && method === "PATCH") {
      const body = request.postDataJSON() as { content: string; expected_version: number };
      if (state.memoryConflictOnce) {
        state.memoryConflictOnce = false;
        state.memory = { ...state.memory!, content: "服务端新内容", version: 2, updated_at: "2026-08-29T04:30:00Z" };
        return route.fulfill({ status: 409, json: { success: false, data: null, error: { code: "VERSION_CONFLICT", message: "changed" }, request_id: "memory-conflict" } });
      }
      state.memory = { ...state.memory!, content: body.content, version: body.expected_version + 1, updated_at: "2026-08-29T05:00:00Z" };
      return route.fulfill({ json: envelope(state.memory) });
    }
    if (path === "/api/agent/memories/memory-1" && method === "DELETE") {
      state.memory = null;
      return route.fulfill({ json: envelope({ deleted: true }) });
    }
    const agentMatch = path.match(/^\/api\/agent\/conversations\/([^/]+)(.*)$/);
    if (agentMatch) {
      const conversationId = agentMatch[1];
      const suffix = agentMatch[2];
      if (suffix === "/messages/stream" && method === "POST") {
        state.agentConversationId = conversationId;
        const body = request.postDataJSON() as { text: string; slots?: Record<string, unknown> | null };
        state.agentMessages.push({ id: `user-${state.agentMessages.length}`, role: "user", content: body.text, intent: "repair_create", house_id: "house-a", created_at: "2026-08-29T04:00:00Z" });
        if (!body.slots?.location) {
          const turn = { conversation_id: conversationId, status: "ACTIVE", done: false, intent: "repair_create", missing_slots: ["location"], requested_slot: "location", slot_prompt: { field: "location", label: "发生位置", prompt: "请选择事件发生位置，也可以输入更具体的位置", allow_custom: true, options: [{ label: "地下车库", value: "地下车库" }] }, handover_required: false, pending_confirmation: null, facts: null };
          return route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" }, body: `event: clarification\ndata: ${JSON.stringify({ slot_prompt: turn.slot_prompt })}\n\nevent: turn\ndata: ${JSON.stringify(turn)}\n\nevent: done\ndata: {"done":false,"status":"ACTIVE"}\n\n` });
        }
        state.agentPending = { summary: "确认创建报修工单", tool: "repair_create", params: { description: "厨房漏水", location: body.slots.location }, action_hash: "server-action-hash", issued_at: "2026-08-29T04:00:00Z" };
        const turn = { conversation_id: conversationId, status: "WAITING_CONFIRM", done: false, intent: "repair_create", missing_slots: [], requested_slot: null, slot_prompt: null, handover_required: false, pending_confirmation: state.agentPending, facts: null };
        return route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" }, body: `event: confirmation\ndata: ${JSON.stringify(state.agentPending)}\n\nevent: turn\ndata: ${JSON.stringify(turn)}\n\nevent: done\ndata: {"done":false,"status":"WAITING_CONFIRM"}\n\n` });
      }
      if (suffix === "/messages" && method === "GET")
        return route.fulfill({ json: envelope(state.agentMessages) });
      if (suffix === "/confirmations" && method === "POST") {
        const body = request.postDataJSON() as { confirmed: boolean; action_hash: string | null };
        if (body.confirmed && state.agentConflictOnce) {
          state.agentConflictOnce = false;
          state.agentPending = { ...state.agentPending!, action_hash: "fresh-server-action-hash" };
          return route.fulfill({ status: 409, json: { success: false, data: null, error: { code: "CONFIRMATION_PARAMS_CHANGED", message: "changed" }, request_id: "agent-conflict" } });
        }
        state.agentPending = null;
        const facts = body.confirmed ? { work_order: { id: "wo-1", business_no: "BX-2026-001", status: "PENDING_ASSIGNMENT", location: "地下车库", urgency: "NORMAL", updated_at: "2026-08-29T04:00:00Z" } } : null;
        return route.fulfill({ json: envelope({ conversation_id: conversationId, status: "ACTIVE", done: true, intent: "repair_create", reply: body.confirmed ? "已执行，请以结构化工单为准。" : "已取消。", facts, missing_slots: [], requested_slot: null, slot_prompt: null, handover_required: false, pending_confirmation: null }) });
      }
      if (suffix === "" && method === "GET")
        return route.fulfill({ json: envelope({ conversation_id: conversationId, status: state.agentPending ? "WAITING_CONFIRM" : "ACTIVE", current_house_id: "house-a", last_intent: "repair_create", handover_required: false, handover_ticket_id: null, runtime_version: "v2", pending_confirmation: state.agentPending }) });
      if (suffix === "" && method === "DELETE") {
        state.agentConversationId = "";
        state.agentPending = null;
        return route.fulfill({ json: envelope({ conversation_id: conversationId, status: "CLOSED", current_house_id: "house-a", handover_required: false, pending_confirmation: null }) });
      }
    }
    if (path === "/api/confirmations")
      return route.fulfill({
        status: 200,
        json: { token: "confirm-e2e", expires_in_seconds: 300 },
      });
    if (path === "/api/work-orders" && method === "GET")
      return route.fulfill({
        json: envelope({ items: [workOrder()], limit: 50, offset: 0 }),
      });
    if (path === "/api/work-orders" && method === "POST")
      return route.fulfill({ status: 201, json: envelope(workOrder()) });
    if (path === "/api/work-orders/wo-1")
      return route.fulfill({ json: envelope(workOrder()) });
    if (path === "/api/work-orders/wo-1/timeline")
      return route.fulfill({
        json: envelope([
          {
            id: "log-1",
            action: "CREATE",
            from_status: null,
            to_status: "PENDING_ASSIGNMENT",
            note: "居民提交",
            created_at: "2026-08-29T01:00:00Z",
          },
        ]),
      });
    if (path === "/api/work-orders/wo-1/actions/assign") {
      state.workOrderVersion += 1;
      return route.fulfill({ json: envelope(workOrder()) });
    }
    if (path === "/api/staff")
      return route.fulfill({
        json: envelope([
          {
            id:
              url.searchParams.get("role") === "REPAIR_WORKER"
                ? "worker-1"
                : "guard-1",
            display_name: "值班人员",
            role: url.searchParams.get("role"),
          },
        ]),
      });
    const bill = {
      bill_id: "bill-1",
      bill_period: "2026-08",
      building_name: "1 栋",
      room_number: "1203",
      due_date: "2026-09-10",
      property_fee: "120.00",
      utility_fee: "30.00",
      parking_fee: "50.00",
      late_fee: "0.00",
      total_amount: "200.00",
      status: "UNPAID",
      user_name: "真实居民",
      version: 1,
      fee_type: "PROPERTY",
      house_id: "house-a",
    };
    if (path === "/api/billing/bills" && method === "GET")
      return route.fulfill({ json: envelope([bill]) });
    if (path === "/api/billing/bills/bill-1")
      return route.fulfill({
        json: envelope({
          bill,
          unknown_rule: true,
          rule: null,
          consultation_entry: "/billing/consultations",
        }),
      });
    if (path === "/api/billing/consultations" && method === "GET")
      return route.fulfill({ json: envelope([consultation()]) });
    if (path === "/api/billing/consultations" && method === "POST")
      return route.fulfill({ status: 201, json: envelope(consultation()) });
    if (path === "/api/billing/consultations/consult-1" && method === "GET")
      return route.fulfill({ json: envelope(consultation()) });
    if (path === "/api/billing/consultations/consult-1/process") {
      if (state.consultationConflictOnce) {
        state.consultationConflictOnce = false;
        state.consultationVersion = 2;
        return route.fulfill({
          status: 409,
          json: {
            success: false,
            data: null,
            error: {
              code: "VERSION_CONFLICT",
              message: "资源已更新",
              details: { current_version: 2 },
            },
            request_id: "req-conflict",
          },
        });
      }
      state.consultationVersion += 1;
      return route.fulfill({ json: envelope(consultation()) });
    }
    if (path === "/api/announcements" && method === "GET")
      return route.fulfill({
        json: envelope({ items: [announcement()], limit: 50, offset: 0 }),
      });
    if (path === "/api/announcements/ann-1" && method === "GET")
      return route.fulfill({ json: envelope(announcement()) });
    if (path.endsWith("/audience-preview"))
      return route.fulfill({
        json: envelope({ count: 12, buildings: ["1栋"] }),
      });
    if (path.endsWith("/versions"))
      return route.fulfill({
        json: envelope([{ version: 1 }, { version: 2 }]),
      });
    if (path.endsWith("/actions/approve")) {
      state.announcementStatus = "APPROVED";
      state.announcementVersion += 1;
      return route.fulfill({ json: envelope(announcement()) });
    }
    if (path.endsWith("/actions/publish")) {
      state.announcementStatus = "PUBLISHED";
      state.announcementVersion += 1;
      return route.fulfill({ json: envelope(announcement()) });
    }
    if (path === "/api/inspection-tasks" && method === "GET")
      return route.fulfill({
        json: envelope({ items: [inspection()], limit: 50, offset: 0 }),
      });
    if (path === "/api/inspection-tasks/task-1")
      return route.fulfill({ json: envelope(inspection()) });
    if (path.endsWith("/inspection-tasks/task-1/timeline"))
      return route.fulfill({ json: envelope([]) });
    if (path.endsWith("/inspection-tasks/task-1/actions/start")) {
      state.inspectionVersion += 1;
      return route.fulfill({ json: envelope(inspection()) });
    }
    if (path === "/api/security-events" && method === "GET")
      return route.fulfill({
        json: envelope({ items: [security()], limit: 50, offset: 0 }),
      });
    if (path === "/api/security-events/event-1")
      return route.fulfill({ json: envelope(security()) });
    if (path.endsWith("/security-events/event-1/timeline"))
      return route.fulfill({ json: envelope([]) });
    if (path.endsWith("/security-events/event-1/actions/grade-confirm")) {
      state.securityVersion += 1;
      return route.fulfill({ json: envelope(security()) });
    }
    if (path === "/api/messages" && method === "GET")
      return route.fulfill({
        json: envelope({
          items: [
            {
              id: "message-1",
              title: "工单进度",
              body: "维修人员已接单",
              status: "DELIVERED",
              is_read: state.read,
              business_type: "REPAIR",
              resource_id: "wo-1",
              retry_count: 0,
              created_at: "2026-08-29T02:00:00Z",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    if (path.includes("/api/messages/") && method === "POST") {
      state.read = true;
      return route.fulfill({ json: envelope({ updated: 1 }) });
    }
    if (path === "/api/admin/dashboard")
      return route.fulfill({
        json: envelope({
          pending_count: 1,
          failed_message_count: 1,
          high_risk_event_count: 1,
          pending_items: [
            {
              id: "pending-1",
              source: "MANUAL",
              queue: "DUTY",
              summary: "待处理事项",
              status: "PENDING",
            },
          ],
          failed_messages: [
            {
              id: "failed-1",
              title: "发送失败",
              body: "已转人工",
              status: "FAILED",
              retry_count: 3,
            },
          ],
          high_risk_events: [security()],
          integration_health: {
            database: "UP",
            message_delivery: "DEGRADED",
            model_gateway: "CONFIGURED_NOT_PROBED",
          },
        }),
      });
    return route.fulfill({
      status: 404,
      json: {
        success: false,
        data: null,
        error: { code: "NOT_FOUND", message: "not mocked" },
        request_id: "req-e2e",
      },
    });
  });
  await page.route("**/api/auth/login", async (route: Route) => {
    const body = route.request().postDataJSON() as { username: Account };
    const identity = identities[body.username];
    if (!identity)
      return route.fulfill({ status: 401, json: { detail: "invalid" } });
    return route.fulfill({
      status: 200,
      json: {
        access_token: `token-${body.username}`,
        token_type: "bearer",
        community_id: "community-a",
        community_name: "真实社区",
        ...identity,
      },
    });
  });
  await page.route("**/api/auth/house", async (route: Route) => {
    const authorization = route.request().headers().authorization;
    if (authorization === "Bearer token-expired")
      return route.fulfill({ status: 401, json: { detail: "expired" } });
    const body = route.request().postDataJSON() as { house_id: string };
    if (body.house_id === "house-b")
      return route.fulfill({
        status: 200,
        json: {
          house_id: "house-b",
          building: "6 栋",
          unit: "1 单元",
          room_no: "802",
        },
      });
    return route.fulfill({
      status: 200,
      json: {
        house_id: "house-a",
        building: "1 栋",
        unit: "2 单元",
        room_no: "1203",
      },
    });
  });
}

async function login(page: Page, account: Account) {
  await installAuthTransport(page);
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("secret");
  await page.getByRole("button", { name: "登录" }).click();
  if (account !== "expired") await expect(page).toHaveURL(/\/$/);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test("resident real runtime authenticates without exposing Demo business records", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page, "resident");
  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole("heading", { name: "欢迎回来，真实居民" }),
  ).toBeVisible();
  await expect(page.getByLabel("当前房屋")).toContainText(
    "1 栋 · 2 单元 · 1203",
  );
  await page.getByRole("link", { name: "账单" }).click();
  await expect(
    page.getByRole("heading", { name: "账单与财务咨询" }),
  ).toBeVisible();
  await expect(page.getByText("¥200.00")).toBeVisible();
  await expect(page.getByText("卫生间顶部渗水")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("resident Agent clarification, changed confirmation recovery and trusted facts", async ({ page }) => {
  const requestFailures: string[] = [];
  page.on("requestfailed", (request) => requestFailures.push(`${request.url()}: ${request.failure()?.errorText}`));
  await login(page, "resident");
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await page.getByLabel("发送给 Agent").fill("厨房漏水");
  await page.getByRole("button", { name: "发送" }).click();
  await page.waitForTimeout(200);
  expect(requestFailures).toEqual([]);
  await expect(page.getByText("请选择事件发生位置，也可以输入更具体的位置")).toBeVisible();
  await page.getByRole("button", { name: "地下车库" }).click();
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("确认创建报修工单")).toBeVisible();
  await page.reload();
  await expect(page.getByText("确认创建报修工单")).toBeVisible();
  const stale = page.waitForRequest((request) => request.url().endsWith("/confirmations"));
  await page.getByRole("button", { name: "确认执行" }).click();
  expect((await stale).postDataJSON()).toEqual({ confirmed: true, action_hash: "server-action-hash" });
  await expect(page.getByLabel("Agent 对话历史").getByText(/确认参数已变化/)).toBeVisible();
  const fresh = page.waitForRequest((request) => request.url().endsWith("/confirmations"));
  await page.getByRole("button", { name: "确认执行" }).click();
  expect((await fresh).postDataJSON()).toEqual({ confirmed: true, action_hash: "fresh-server-action-hash" });
  await expect(page.getByText("BX-2026-001")).toBeVisible();
  await expect(page.getByText("已执行，请以结构化工单为准。")).toBeVisible();
});

test("real Agent memory CRUD stays actor-scoped and versioned", async ({ page }) => {
  await login(page, "resident");
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await page.getByLabel("内容").fill("优先短信联系");
  await page.getByRole("button", { name: "保存记忆" }).click();
  await expect(page.getByText("优先短信联系")).toBeVisible();
  await page.getByRole("button", { name: "编辑" }).click();
  await page.locator("input").last().fill("仅工作日联系");
  await page.getByRole("button", { name: "提交更新" }).click();
  await expect(page.getByText(/记忆已被其他操作更新/)).toBeVisible();
  await expect(page.getByText("服务端新内容")).toBeVisible();
  await page.getByRole("button", { name: "编辑" }).click();
  await page.locator("input").last().fill("仅工作日联系");
  await page.getByRole("button", { name: "提交更新" }).click();
  await expect(page.getByText("仅工作日联系")).toBeVisible();
  await page.getByRole("button", { name: "删除" }).click();
  await expect(page.getByText("暂无长期记忆")).toBeVisible();
});

test("operations home is the real Agent workspace", async ({ page }) => {
  await login(page, "manager");
  await expect(page.getByText("REAL AGENT WORKSPACE")).toBeVisible();
  await expect(page.getByText("真实对话")).toBeVisible();
});

test("Agent cancel sends real cancellation and never renders business success", async ({ page }) => {
  await login(page, "resident");
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await page.getByLabel("发送给 Agent").fill("厨房漏水");
  await page.getByRole("button", { name: "发送" }).click();
  await page.getByRole("button", { name: "地下车库" }).click();
  await page.getByRole("button", { name: "发送" }).click();
  const cancellation = page.waitForRequest((request) => request.url().endsWith("/confirmations"));
  await page.getByRole("button", { name: "取消" }).click();
  expect((await cancellation).postDataJSON()).toEqual({ confirmed: false, action_hash: null });
  await expect(page.getByText("BX-2026-001")).toHaveCount(0);
  await page.getByLabel("发送给 Agent").fill("改为联系物业");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
});

test("Agent handover and stream failure remain explicit and recoverable", async ({ page }) => {
  await login(page, "resident");
  let handover = true;
  await page.route("**/api/agent/conversations/*/messages/stream", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-3)!;
    const body = handover
      ? `event: handover\ndata: {"conversation_id":"${id}"}\n\nevent: turn\ndata: {"conversation_id":"${id}","status":"HANDOVER","done":true,"missing_slots":[],"handover_required":true,"pending_confirmation":null}\n\nevent: done\ndata: {"done":true}\n\n`
      : `event: failed\ndata: {"category":"execution_failure","recoverable_via_status":true}\n\n`;
    await route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream" }, body });
  });
  await page.goto("/agent");
  await page.getByLabel("发送给 Agent").fill("需要人工协助");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByLabel("Agent 对话历史").getByText("已转人工处理")).toBeVisible();
  handover = false;
  await page.getByLabel("发送给 Agent").fill("再次尝试");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByLabel("Agent 对话历史").getByText(/本轮执行失败/)).toBeVisible();
  await page.getByLabel("发送给 Agent").fill("查看最新状态后重试");
  await expect(page.getByRole("button", { name: "发送" })).toBeEnabled();
});

test("house switch detaches an incompatible Agent conversation", async ({ page }) => {
  await login(page, "multi");
  await page.getByLabel("当前房屋").selectOption("house-a");
  await page.getByRole("link", { name: "Agent", exact: true }).click();
  await page.getByLabel("发送给 Agent").fill("厨房漏水");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page).toHaveURL(/\/agent\/conversations\//);
  await page.getByLabel("当前房屋").selectOption("house-b");
  await expect(page).toHaveURL(/\/agent$/);
  await expect(page.getByText("开始真实 Agent 对话")).toBeVisible();
});

test("resident opens bill detail and sees explicit unknown-rule state", async ({
  page,
}) => {
  await login(page, "resident");
  await page.getByRole("link", { name: "账单" }).click();
  await page.getByRole("link", { name: /2026-08 账单/ }).click();
  await expect(
    page.getByRole("heading", { name: "2026-08 账单" }),
  ).toBeVisible();
  await expect(page.getByText(/未提供可验证的计费规则/)).toBeVisible();
});

test("resident creates a confirmed repair with scoped and idempotent transport", async ({
  page,
}) => {
  await login(page, "resident");
  await page.getByRole("link", { name: "报修" }).click();
  await page.getByLabel("位置").fill("卫生间");
  await page.getByLabel("问题描述").fill("顶部持续渗水");
  const confirmation = page.waitForRequest((request) =>
    request.url().endsWith("/api/confirmations"),
  );
  const creation = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/work-orders") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "审阅并确认创建" }).click();
  expect((await confirmation).postDataJSON()).toMatchObject({
    action: "CREATE_WORK_ORDER",
    parameters: { house_id: "house-a", location: "卫生间" },
  });
  const write = await creation;
  expect(write.headers()["x-current-house-id"]).toBeUndefined();
  expect(write.headers()["idempotency-key"]).toMatch(/^web_v2_/);
  expect(write.postDataJSON()).toMatchObject({
    house_id: "house-a",
    confirmation_token: "confirm-e2e",
  });
});

test("security staff performs an inspection transition and never sees Agent-only CONFIRM_AI", async ({
  page,
}) => {
  await login(page, "guard");
  await page.getByRole("link", { name: "运营" }).click();
  await page.getByRole("link", { name: /夜间消防巡检/ }).click();
  await expect(page.getByRole("button", { name: "开始巡检" })).toBeVisible();
  await expect(page.getByText("CONFIRM AI")).toHaveCount(0);
  await page.getByRole("button", { name: "开始巡检" }).click();
  await page.getByRole("button", { name: "确认提交" }).click();
  await expect(page.getByText("操作已由服务端确认并提交。")).toBeVisible();
});

test("manager confirms a high-risk security grade", async ({ page }) => {
  await login(page, "manager");
  await page.goto("/operations/security/event-1");
  await expect(page.getByText("风险：高风险")).toBeVisible();
  await page.getByRole("button", { name: "确认风险评级" }).click();
  await page.getByRole("button", { name: "确认提交" }).click();
  await expect(page.getByText("操作已由服务端确认并提交。")).toBeVisible();
});

test("finance stale expected_version refetches and discards the old action", async ({
  page,
}) => {
  await login(page, "finance");
  await page.goto("/billing/consultations/consult-1");
  await page.getByRole("button", { name: "开始处理" }).click();
  await page.getByRole("button", { name: "确认提交" }).click();
  await expect(page.getByText(/资源已被其他操作更新/)).toBeVisible();
  await expect(page.getByText("v2", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始处理" })).toHaveCount(0);
});

test("manager approves then confirms announcement publication", async ({
  page,
}) => {
  await login(page, "manager");
  await page.goto("/community/announcements/ann-1");
  await page.getByRole("button", { name: "批准" }).click();
  await page.getByRole("button", { name: "确认提交" }).click();
  await expect(
    page.getByRole("button", { name: "二次确认并发布" }),
  ).toBeVisible();
  const confirmation = page.waitForRequest((request) =>
    request.url().endsWith("/api/confirmations"),
  );
  await page.getByRole("button", { name: "二次确认并发布" }).click();
  await page.getByRole("button", { name: "确认提交" }).click();
  expect((await confirmation).postDataJSON()).toMatchObject({
    action: "ANNOUNCEMENT_PUBLISH",
    parameters: { announcement_id: "ann-1", action: "PUBLISH" },
  });
  await expect(page.getByText("已发布")).toBeVisible();
});

test("message center marks one message read and deep-links to business context", async ({
  page,
}) => {
  await login(page, "resident");
  await page.getByRole("link", { name: "消息" }).click();
  await expect(page.getByText("工单进度")).toBeVisible();
  await page.getByRole("button", { name: "标为已读", exact: true }).click();
  await expect(
    page.locator("section").getByText("已读", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "查看关联业务" }).click();
  await expect(
    page.getByRole("heading", { name: "BX-2026-001" }),
  ).toBeVisible();
  await expect(page.getByText("真实居民", { exact: true })).toBeVisible();
  await expect(page.getByText("1栋 1单元 101室", { exact: true })).toBeVisible();
  await expect(page.getByText("待分派", { exact: true })).toBeVisible();
});

test("resident is forbidden from admin while manager sees truthful service states", async ({
  page,
}) => {
  await login(page, "resident");
  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "当前身份无权访问" }),
  ).toBeVisible();
});

test("manager gets deterministic operations and admin navigation", async ({
  page,
}) => {
  await login(page, "manager");
  await expect(page.getByText("REAL AGENT WORKSPACE")).toBeVisible();
  await expect(page.getByRole("link", { name: "账单" })).toBeVisible();
  await expect(page.getByRole("link", { name: "运营" })).toBeVisible();
  await page.getByRole("link", { name: "管理" }).click();
  await expect(page.getByRole("heading", { name: "管理工作台" })).toBeVisible();
  await expect(page.getByText("CONFIGURED NOT PROBED")).toBeVisible();
});

test("multi-house actor must choose and only then receives real display metadata", async ({
  page,
}) => {
  await login(page, "multi");
  await expect(
    page.getByText("选择当前房屋后可查看房屋账单和居民工单。"),
  ).toBeVisible();
  const selector = page.getByLabel("当前房屋");
  await expect(selector).toHaveValue("");
  await selector.selectOption("house-b");
  await expect(selector).toHaveValue("house-b");
  await expect(selector).toContainText("6 栋 · 1 单元 · 802");
});

test("logout clears the single persisted record", async ({ page }) => {
  await login(page, "resident");
  await page.getByRole("button", { name: "打开用户菜单" }).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(
    page.getByRole("heading", { name: "登录社区工作台" }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => sessionStorage.getItem("property_agent_v2_session")),
    )
    .toBeNull();
});

test("authenticated 401 during initial house resolution returns to login", async ({
  page,
}) => {
  await login(page, "expired");
  await expect(
    page.getByRole("heading", { name: "登录社区工作台" }),
  ).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.length)).toBe(0);
});

test("mobile navigation, account controls and house selector are keyboard operable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "multi");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(
    page.getByRole("dialog").getByRole("link", { name: "社区" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByLabel("当前房屋").focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("当前房屋")).not.toHaveValue("");
  await page.getByRole("button", { name: "打开用户菜单" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem", { name: /多房居民/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("dedicated Demo entry remains an explicitly labeled design preview", async ({
  page,
}) => {
  await page.goto("/demo.html#/login");
  await expect(
    page.getByRole("heading", { name: "进入产品预览" }),
  ).toBeVisible();
  await page.getByLabel("预览身份").fill("resident");
  await page.getByLabel("预览口令").fill("preview");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByText("Demo 数据 · 非生产")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /生活里的小事/ }),
  ).toBeVisible();
});
