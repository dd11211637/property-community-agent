import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const baseURL = process.env.RC_E2E_BASE_URL ?? "http://127.0.0.1:18080";

type BrowserSession = {
  accessToken: string;
  currentHouseId: string | null;
};

async function login(page: Page, username: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("账号").fill(username);
  await page.getByLabel("密码").fill("123456");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function browserSession(page: Page): Promise<BrowserSession> {
  return page.evaluate(() => {
    const raw = sessionStorage.getItem("property_agent_v2_session");
    if (!raw) throw new Error("authenticated browser session was not persisted");
    const record = JSON.parse(raw) as { session: BrowserSession };
    return record.session;
  });
}

async function authorityGet(
  request: APIRequestContext,
  page: Page,
  path: string,
) {
  const session = await browserSession(page);
  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.accessToken}`,
  };
  if (session.currentHouseId) headers["X-Current-House-ID"] = session.currentHouseId;
  return request.get(`${baseURL}${path}`, { headers });
}

test.describe.serial("Frontend V2 real Release Candidate stack", () => {
  test("resident authenticates, creates a confirmed repair, and authority API persists it", async ({
    page,
    request,
  }) => {
    const marker = `RC验收-${Date.now()}`;
    await login(page, "zhangsan");
    await expect(page.getByRole("heading", { name: "欢迎回来，张三" })).toBeVisible();
    await expect(page.getByLabel("当前房屋")).not.toHaveValue("");

    await page.getByRole("link", { name: "报修" }).click();
    await page.getByLabel("位置").fill("验收卫生间");
    await page.getByLabel("问题描述").fill(marker);
    await page.getByRole("button", { name: "审阅并确认创建" }).click();

    await expect.poll(async () => {
      const response = await authorityGet(request, page, "/api/work-orders?limit=100&offset=0");
      expect(response.ok()).toBeTruthy();
      const body = (await response.json()) as { data: { items: Array<{ description: string }> } };
      return body.data.items.some((item) => item.description === marker);
    }).toBe(true);
  });

  test("Agent POST SSE and Memory use the real edge and authoritative persistence", async ({
    page,
    request,
  }) => {
    const memory = `验收偏好-${Date.now()}`;
    await login(page, "zhangsan");
    await page.getByRole("link", { name: "Agent", exact: true }).click();
    await page.getByLabel("发送给 Agent").fill("查询我当前房屋的报修记录");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page).toHaveURL(/\/agent\/conversations\//);
    await expect(page.getByLabel("Agent 对话历史")).toBeVisible();

    await expect.poll(async () => {
      const response = await authorityGet(request, page, "/api/agent/conversations");
      if (!response.ok()) return 0;
      const body = (await response.json()) as { data: unknown[] };
      return body.data.length;
    }).toBeGreaterThan(0);

    await page.getByRole("link", { name: "AI 与记忆", exact: true }).click();
    await page.getByLabel("内容").fill(memory);
    await page.getByRole("button", { name: "保存记忆" }).click();
    await expect.poll(async () => {
      const response = await authorityGet(request, page, "/api/agent/memories");
      if (!response.ok()) return false;
      const body = (await response.json()) as { data: Array<{ content: string }> };
      return body.data.some((item) => item.content === memory);
    }).toBe(true);
  });

  test("multi-house selection is server validated before scoped data loads", async ({ page }) => {
    await login(page, "lisi");
    const selector = page.getByLabel("当前房屋");
    await expect(selector).toHaveValue("");
    const options = await selector.locator("option").evaluateAll((items) =>
      items.map((item) => (item as HTMLOptionElement).value).filter(Boolean),
    );
    expect(options.length).toBeGreaterThan(1);
    await selector.selectOption(options[0]);
    await expect(selector).toHaveValue(options[0]);
    const session = await browserSession(page);
    expect(session.currentHouseId).toBe(options[0]);
  });

  test("resident message read is persisted by the actor-scoped authority API", async ({
    page,
    request,
  }) => {
    await login(page, "zhangsan");
    await page.getByRole("link", { name: "消息" }).click();
    const markRead = page.getByRole("button", { name: "标为已读", exact: true });
    await expect(markRead.first()).toBeVisible();
    await markRead.first().click();
    await expect.poll(async () => {
      const response = await authorityGet(request, page, "/api/messages?limit=50&offset=0");
      if (!response.ok()) return false;
      const body = (await response.json()) as { data: { items: Array<{ is_read: boolean }> } };
      return body.data.items.some((item) => item.is_read);
    }).toBe(true);
  });

  test("finance advances a real consultation with expected-version protection", async ({
    page,
    request,
  }) => {
    await login(page, "finance");
    await page.goto("/billing/consultations/demo-consultation-01");
    await page.getByRole("button", { name: "开始处理" }).click();
    await page.getByRole("button", { name: "确认提交" }).click();
    await expect.poll(async () => {
      const response = await authorityGet(
        request,
        page,
        "/api/billing/consultations/demo-consultation-01",
      );
      if (!response.ok()) return "";
      const body = (await response.json()) as { data: { status: string } };
      return body.data.status;
    }).toBe("PROCESSING");
  });

  test("security guard starts the assigned inspection through the real state machine", async ({
    page,
    request,
  }) => {
    const taskId = "c3000000-0000-0000-0000-000000000001";
    await login(page, "security_guard");
    await page.goto(`/operations/inspections/${taskId}`);
    await page.getByRole("button", { name: "开始巡检" }).click();
    await page.getByRole("button", { name: "确认提交" }).click();
    await expect.poll(async () => {
      const response = await authorityGet(request, page, `/api/inspection-tasks/${taskId}`);
      if (!response.ok()) return "";
      const body = (await response.json()) as { data: { status: string } };
      return body.data.status;
    }).toBe("IN_PROGRESS");
  });

  test("manager approves a pending announcement through the real confirmation path", async ({
    page,
    request,
  }) => {
    const announcementId = "c2000000-0000-0000-0000-000000000001";
    await login(page, "manager");
    await page.goto(`/community/announcements/${announcementId}`);
    await page.getByRole("button", { name: "批准" }).click();
    await page.getByRole("button", { name: "确认提交" }).click();
    await expect.poll(async () => {
      const response = await authorityGet(
        request,
        page,
        `/api/announcements/${announcementId}`,
      );
      if (!response.ok()) return "";
      const body = (await response.json()) as { data: { status: string } };
      return body.data.status;
    }).toBe("APPROVED");
  });

  test("manager reaches operations and read-only admin through real authorization", async ({
    page,
    request,
  }) => {
    await login(page, "manager");
    await expect(page.getByRole("link", { name: "运营" })).toBeVisible();
    await page.getByRole("link", { name: "管理" }).click();
    await expect(page.getByRole("heading", { name: "管理工作台" })).toBeVisible();
    const response = await authorityGet(request, page, "/api/admin/dashboard");
    expect(response.status()).toBe(200);
    const body = (await response.json()) as { data: { integration_health: object } };
    expect(body.data.integration_health).toBeTruthy();
  });
});
