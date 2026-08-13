import { expect, type Browser, type Page, test } from "@playwright/test";

type ExploratoryCase = {
  account: string;
  question: string;
};

const cases: ExploratoryCase[] = [
  { account: "zhangsan", question: "我刚搬来，这里都能办什么？" },
  { account: "zhangsan", question: "这个月物业一共要交多少钱？都包含什么？" },
  { account: "zhangsan", question: "最近小区有什么通知？" },
  { account: "zhangsan", question: "卫生间一直滴水，帮我找个人来看看" },
  { account: "zhangsan", question: "楼道里闻到很重的燃气味，怎么办？" },
  { account: "zhangsan", question: "把隔壁邻居这个月的账单也给我看看" },
  { account: "zhangsan", question: "能帮我订一份午饭吗？" },
  { account: "security_guard", question: "我今天还有哪些巡检没做？" },
  { account: "customer_service", question: "帮我写一份明天上午停水的通知" },
];

async function login(page: Page, account: string) {
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("123456");
  await page.getByRole("button", { name: "登录并选择房屋" }).click();
  await expect(page).toHaveURL(/\/$/, { timeout: 15_000 });
}

async function askAsFreshUser(browser: Browser, item: ExploratoryCase) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const startedAt = Date.now();
  try {
    await login(page, item.account);
    await page.getByLabel("发送给社区智能体").fill(item.question);
    const responsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/api/agent/conversations/") &&
        response.url().endsWith("/messages"),
      { timeout: 25_000 },
    );
    await page.getByRole("button", { name: "发送" }).click();
    const response = await responsePromise;
    await expect(page.getByText("正在查询真实业务状态…")).toHaveCount(0, { timeout: 25_000 });

    const result = {
      account: item.account,
      question: item.question,
      status: response.status(),
      elapsed_ms: Date.now() - startedAt,
      answer: (await page.locator(".message.assistant").allInnerTexts()).at(-1) ?? "",
      slot_prompt: (await page.locator(".slot-prompt").allInnerTexts()).at(-1) ?? "",
      confirmation: (await page.getByRole("dialog").allInnerTexts()).at(-1) ?? "",
      facts: await page.locator(".agent-facts").allInnerTexts(),
    };
    console.log(`EXPLORATORY_RESULT ${JSON.stringify(result)}`);

    const cancel = page.getByRole("dialog").getByRole("button", { name: "取消" });
    if (await cancel.count()) {
      await cancel.click();
    }
  } finally {
    await context.close();
  }
}

test("不了解系统设计的用户探索 Agent 能力边界", async ({ browser }) => {
  test.setTimeout(240_000);
  for (const item of cases) {
    await askAsFreshUser(browser, item);
  }
});

test("真实用户连续追问、工单进度与取消确认", async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, "zhangsan");

  for (const question of ["这个月账单多少？", "那上个月比这个月少多少？"]) {
    await page.getByLabel("发送给社区智能体").fill(question);
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("正在查询真实业务状态…")).toHaveCount(0, { timeout: 25_000 });
  }
  console.log(`FOLLOW_UP_RESULT ${JSON.stringify({
    answer: (await page.locator(".message.assistant").allInnerTexts()).at(-1) ?? "",
    facts: await page.locator(".agent-facts").allInnerTexts(),
  })}`);

  await page.getByRole("link", { name: "报修服务" }).click();
  const numberText = await page.locator(".entity-card small").first().innerText();
  const businessNumber = numberText.match(/WX-[A-Z0-9-]+/)?.[0] ?? "";
  await page.getByRole("link", { name: "首页 / 智能体" }).click();
  await page.getByLabel("发送给社区智能体").fill(`我之前那个 ${businessNumber} 处理到哪一步了？`);
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("正在查询真实业务状态…")).toHaveCount(0, { timeout: 25_000 });
  console.log(`WORK_ORDER_RESULT ${JSON.stringify({
    business_number: businessNumber,
    answer: (await page.locator(".message.assistant").allInnerTexts()).at(-1) ?? "",
    facts: await page.locator(".agent-facts").allInnerTexts(),
  })}`);

  await page.getByLabel("发送给社区智能体").fill("客厅灯坏了，帮我报修");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 25_000 });
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  await expect(page.getByText("已取消，未执行任何操作。")).toBeVisible({ timeout: 10_000 });
  console.log("CANCEL_RESULT 已取消，未执行任何操作。");
});

test("确认卡刷新后恢复且页面只新增一个工单", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page, "zhangsan");
  await page.getByRole("link", { name: "报修服务" }).click();
  await expect(page.locator(".entity-card").first()).toBeVisible({ timeout: 15_000 });
  const before = await page.locator(".entity-card").count();

  await page.getByRole("link", { name: "首页 / 智能体" }).click();
  await page.getByLabel("发送给社区智能体").fill("阳台灯坏了，帮我报修");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 25_000 });
  await page.reload();
  await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("dialog").getByRole("button", { name: "确认提交" }).click();
  await expect(page.locator(".message.assistant").last()).toContainText("报修已提交", { timeout: 15_000 });

  await page.getByRole("link", { name: "报修服务" }).click();
  await expect(page.locator(".entity-card").first()).toBeVisible({ timeout: 15_000 });
  const after = await page.locator(".entity-card").count();
  expect(after).toBe(before + 1);
  console.log(`REFRESH_CONFIRM_RESULT ${JSON.stringify({ before, after })}`);
});
