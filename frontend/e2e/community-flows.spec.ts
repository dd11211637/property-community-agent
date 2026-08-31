import { expect, type Page, test } from "@playwright/test";

async function login(page: Page, account: string, houseSelectionRequired = false) {
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("123456");
  await page.getByRole("button", { name: "登录并选择房屋" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", {
    name: houseSelectionRequired ? "请先选择要服务的房屋" : "今天想处理什么？",
  })).toBeVisible();
}

async function authenticatedHeaders(page: Page) {
  const session = await page.evaluate(() => ({
    token: sessionStorage.getItem("property_agent_token"),
    houseId: sessionStorage.getItem("property_agent_house_id"),
  }));
  expect(session.token).toBeTruthy();
  expect(session.houseId).toBeTruthy();
  return {
    Authorization: `Bearer ${session.token}`,
    "X-Current-House-ID": session.houseId!,
  };
}

async function waitForAgent(page: Page) {
  await expect(page.getByText("正在查询真实业务状态…")).toHaveCount(0, { timeout: 15_000 });
}

async function completeRepairAppointment(page: Page) {
  const appointmentInput = page.getByLabel("发送给社区智能体");
  await expect(appointmentInput).toHaveAttribute("type", "datetime-local");
  await appointmentInput.fill("2026-09-02T10:30");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
}

test("住户登录后可查看真实账单并通过 Agent 查询", async ({ page }) => {
  await login(page, "zhangsan");
  await expect(page.getByLabel("当前房屋")).toHaveText("1栋 1单元 101");

  await page.getByRole("link", { name: "账单费用" }).click();
  await expect(page.getByRole("heading", { name: "当前房屋账单" })).toBeVisible();
  const firstBill = page.locator(".bill-card").first();
  await expect(firstBill).toBeVisible();
  await firstBill.click();
  await expect(page.getByRole("heading", { name: "账单详情" })).toBeVisible();
  await expect(page.getByText("费用规则", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "首页 / 智能体" }).click();
  await page.getByLabel("发送给社区智能体").fill("查一下我的账单");
  const streamResponsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/agent/conversations/") &&
    response.url().endsWith("/messages/stream"),
  );
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  const streamResponse = await streamResponsePromise;
  expect(streamResponse.headers()["content-type"]).toContain("text/event-stream");
  const streamBody = await streamResponse.text();
  expect(streamBody).toContain("event: turn");
  expect(streamBody).toContain("event: done");
  await expect(page.locator(".message.assistant").last()).toContainText(/账单|查询/);
  const firstAgentBill = page.locator(".agent-facts").first();
  await expect(firstAgentBill).toContainText("当前房屋账单");
  await expect(firstAgentBill).toContainText("¥430.00");
  await expect(firstAgentBill).not.toContainText("¥-");
});

test("Agent 查询本月账单只返回当前月份", async ({ page }) => {
  await login(page, "zhangsan");

  const periodParts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(new Date());
  const year = periodParts.find((part) => part.type === "year")?.value ?? "";
  const month = (periodParts.find((part) => part.type === "month")?.value ?? "").padStart(2, "0");
  const currentPeriod = `${year}-${month}`;
  await page.getByLabel("发送给社区智能体").fill("查询这个月的账单");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);

  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText(currentPeriod);
  await expect(response.locator(".agent-facts")).toHaveCount(1);
});

test("Agent 能结合上一轮理解上个月并展示费用明细", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByLabel("发送给社区智能体").fill("查询这个月的账单");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await expect(page.locator(".message.assistant").last()).toContainText("账单共 430.00 元");

  await page.getByLabel("发送给社区智能体").fill("那上个月呢");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);

  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText("物业费 250.00 元");
  await expect(response).toContainText("水电费 30.00 元");
  await expect(response).toContainText("停车费 150.00 元");
});

test("Agent 查询今日停水公告会说明范围和空结果边界", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByLabel("发送给社区智能体").fill("今天会停水吗");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);

  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText("已发布停水公告");
  await expect(response).toContainText("现有公告记录");
  await expect(response).toContainText("临时故障");
});

test("Agent 查询社区资料时只基于已发布来源且不编造电话", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByLabel("发送给社区智能体").fill("物业电话是多少");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText("已发布正式资料");
  await expect(response).toContainText("物业工作人员确认");
  await expect(response).not.toContainText(/\d{7,}/);
});

test("安保通过 Agent 获取本人巡检完成度且卡片不误标公告", async ({ page }) => {
  await login(page, "security_guard");
  await page.getByLabel("发送给社区智能体").fill("巡检任务都完成了吗");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);

  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText(/巡检任务|全部完成|未完成/);
  await expect(response.getByText("巡检完成情况")).toBeVisible();
  await expect(response).toContainText(/共 \d+ 项，已完成 \d+ 项/);
  await expect(response.getByText("已发布公告")).toHaveCount(0);
});

test("住户通过 Agent 确认上报高风险事件后进入人工接管", async ({ page }) => {
  await login(page, "zhangsan");
  const marker = `1栋厨房-E2E燃气-${Date.now()}`;
  await page.getByLabel("发送给社区智能体").fill(`${marker}闻到强烈燃气味，请上报事件`);
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);

  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("高风险提示");
  await expect(dialog).toContainText("燃气泄漏");
  await expect(dialog).toContainText("高风险");
  await dialog.getByRole("button", { name: "确认提交" }).click();

  const response = page.locator(".message.assistant").last();
  await expect(response).toContainText(/事件编号 AQ-/);
  await expect(response).toContainText("人工处置");
  await expect(response.locator(".agent-facts")).toContainText("安防事件");
});

test("报修确认框只展示住户可理解的中文字段", async ({ page }) => {
  await login(page, "zhangsan");

  await page.getByLabel("发送给社区智能体").fill("客厅电灯不亮了");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await completeRepairAppointment(page);

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "确认提交这条报修吗？" })).toBeVisible();
  await expect(dialog.getByText("问题类型", { exact: true })).toBeVisible();
  await expect(dialog.getByText("具体位置", { exact: true })).toBeVisible();
  await expect(dialog.getByText("问题描述", { exact: true })).toBeVisible();
  await expect(dialog.getByText("category", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("location", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("description", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("action", { exact: true })).toHaveCount(0);
});

test("报修信息不完整时可逐步点击选项补全", async ({ page }) => {
  await login(page, "zhangsan");

  await page.getByLabel("发送给社区智能体").fill("我要报修");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await expect(page.getByText("请描述一下具体出现了什么故障？")).toBeVisible();
  await page.getByLabel("发送给社区智能体").fill("灯具损坏");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await expect(page.getByText("这个故障发生在哪里？")).toBeVisible();
  await page.getByRole("button", { name: "客厅", exact: true }).click();
  await waitForAgent(page);
  await completeRepairAppointment(page);
  await expect(page.getByRole("heading", { name: "确认提交这条报修吗？" })).toBeVisible();
  await expect(page.getByText(/缺失：|请提供：/)).toHaveCount(0);
});

test("可使用业务工单号查询真实状态和进度", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByRole("link", { name: "报修服务" }).click();
  const businessNumberText = await page.locator(".repair-item small").first().innerText();
  const businessNumber = businessNumberText.match(/WX-[A-Z0-9-]+/)?.[0];
  expect(businessNumber).toBeTruthy();

  await page.getByRole("link", { name: "首页 / 智能体" }).click();
  await page.getByLabel("发送给社区智能体").fill(`查询工单进度 ${businessNumber}`);
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  const result = page.locator(".agent-facts").last();
  await expect(result).toContainText(businessNumber!, { timeout: 15_000 });
  await expect(result).toContainText(/待接单|等待|处理中|返工|完成/, { timeout: 15_000 });
});

test("多房屋住户必须选择房屋且切换后服务可用", async ({ page }) => {
  await login(page, "lisi", true);

  const picker = page.getByLabel("当前房屋");
  await expect(picker).toHaveValue("");
  await expect(picker).toBeFocused();
  await expect(page.getByText(/Multiple houses available/)).toHaveCount(0);
  await expect(picker.locator("option")).toHaveCount(3);
  await picker.selectOption({ index: 2 });
  await expect(picker).not.toHaveValue("");
  await expect(picker.locator("option:checked")).toHaveText("2栋 1单元 201");

  await page.getByRole("link", { name: "账单费用" }).click();
  await expect(page.getByRole("heading", { name: "当前房屋账单" })).toBeVisible();
  await expect(page.locator(".bill-card").first()).toBeVisible();
});

test("管理者看到真实聚合工作台，住户访问则被拒绝", async ({ browser }) => {
  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "管理工作台", exact: true }).click();
  await expect(managerPage.getByRole("heading", { name: "管理工作台" })).toBeVisible();
  await expect(managerPage.getByText("服务支撑状态")).toBeVisible();
  await managerContext.close();

  const residentContext = await browser.newContext();
  const residentPage = await residentContext.newPage();
  await login(residentPage, "zhangsan");
  await residentPage.goto("/admin");
  await expect(residentPage.getByRole("alert")).toContainText(/无权|权限|角色|禁止/);
  await residentContext.close();
});

test("管理者派单时选择工作人员姓名而不是填写 UUID", async ({ browser }) => {
  const marker = `E2E人员选择-${Date.now()}`;
  const residentContext = await browser.newContext();
  const residentPage = await residentContext.newPage();
  await login(residentPage, "zhangsan");
  await residentPage.getByRole("link", { name: "报修服务" }).click();
  await residentPage.getByRole("button", { name: "新建报修" }).click();
  await residentPage.getByLabel("具体位置").fill("客厅");
  await residentPage.getByLabel("问题描述").fill(marker);
  await residentPage.getByRole("button", { name: "核对并提交" }).click();
  await residentPage.getByRole("dialog").getByRole("button", { name: "确认提交" }).click();
  await residentContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "报修服务" }).click();
  await managerPage.getByRole("button", { name: new RegExp(marker) }).click();
  await managerPage.getByRole("button", { name: "派单", exact: true }).click();

  const dialog = managerPage.getByRole("dialog");
  await expect(dialog.getByLabel("维修人员")).toHaveValue("a2000000-0000-0000-0000-000000000020");
  await expect(dialog.getByLabel("维修人员").locator("option")).toHaveText(["维修工老张"]);
  await expect(dialog.getByText(/UUID/)).toHaveCount(0);
  await dialog.getByRole("button", { name: "取消" }).click();
  await managerContext.close();
});

test("住户创建财务咨询后刷新仍可见并可提交", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByRole("link", { name: "账单费用" }).click();
  await page.getByRole("button", { name: "发起财务咨询" }).click();
  const dialog = page.getByRole("dialog");
  const marker = `E2E费用咨询-${Date.now()}`;
  await dialog.getByLabel("咨询主题").fill(marker);
  await dialog.getByLabel("问题描述").fill("请核对本月物业费计算依据。");
  await dialog.getByRole("button", { name: "确认操作" }).click();

  await expect(page.getByText(marker)).toBeVisible();
  await page.reload();
  await expect(page.getByText(marker)).toBeVisible();
  await page.getByText(marker).click();
  await page.getByRole("button", { name: "提交咨询" }).click();
  await expect(page.getByRole("button", { name: new RegExp(`待复核.*${marker}`) })).toBeVisible();
});

test("住户可人工上报安防事件且刷新后状态保留", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByRole("link", { name: "巡检与事件" }).click();
  await page.getByRole("button", { name: "人工上报事件" }).click();
  const dialog = page.getByRole("dialog");
  const location = `1栋大厅-E2E-${Date.now()}`;
  await dialog.getByLabel("发生位置").fill(location);
  await dialog.getByLabel("事件描述").fill("发现消防通道有杂物，需要处置。");
  await dialog.getByRole("button", { name: "核对并上报" }).click();

  await expect(page.getByRole("heading", { name: location }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: location }).first()).toBeVisible();
});

test("客服创建公告草稿并送审，管理者可看到待审核状态", async ({ browser }) => {
  const title = `E2E社区公告-${Date.now()}`;
  const customerContext = await browser.newContext();
  const customerPage = await customerContext.newPage();
  await login(customerPage, "customer_service");
  await customerPage.getByRole("link", { name: "社区公告" }).click();
  await customerPage.getByRole("button", { name: "新建草稿" }).click();
  await customerPage.getByLabel("标题").fill(title);
  await customerPage.getByLabel("正文").fill("本周六进行公共区域设备巡检，请居民知悉。");
  await customerPage.getByRole("button", { name: "保存草稿" }).click();
  await expect(customerPage.getByRole("heading", { name: title }).last()).toBeVisible();
  await customerPage.getByRole("button", { name: "送审" }).click();
  await customerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await expect(customerPage.getByRole("button", { name: new RegExp(`待审核.*${title}`) })).toBeVisible();
  await customerContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "社区公告" }).click();
  await expect(managerPage.getByText(title)).toBeVisible();
  await managerContext.close();
});

test("报修跨角色完成派单、返工、验收和评价闭环", async ({ browser }) => {
  const marker = `E2E返工闭环-${Date.now()}`;

  const residentContext = await browser.newContext();
  const residentPage = await residentContext.newPage();
  await login(residentPage, "zhangsan");
  await residentPage.getByRole("link", { name: "报修服务" }).click();
  await residentPage.getByRole("button", { name: "新建报修" }).click();
  await residentPage.getByLabel("具体位置").fill("客厅");
  await residentPage.getByLabel("问题描述").fill(marker);
  await residentPage.getByRole("button", { name: "核对并提交" }).click();
  await residentPage.getByRole("dialog").getByRole("button", { name: "确认提交" }).click();
  await expect(residentPage.getByRole("button", { name: new RegExp(marker) })).toBeVisible();
  await residentContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "报修服务" }).click();
  await managerPage.getByRole("button", { name: new RegExp(marker) }).click();
  await managerPage.getByRole("button", { name: "派单", exact: true }).click();
  const assignDialog = managerPage.getByRole("dialog");
  await assignDialog.getByLabel("维修人员").selectOption({ label: "维修工老张" });
  await assignDialog.getByRole("button", { name: "确认操作" }).click();
  await expect(managerPage.getByText("待接单", { exact: true }).last()).toBeVisible();
  await managerContext.close();

  const workerContext = await browser.newContext();
  const workerPage = await workerContext.newPage();
  await login(workerPage, "repair_worker");
  await workerPage.getByRole("link", { name: "报修服务" }).click();
  await workerPage.getByRole("button", { name: new RegExp(marker) }).click();
  await workerPage.getByRole("button", { name: "接单", exact: true }).click();
  await workerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await workerPage.getByRole("button", { name: "提交完工" }).click();
  await workerPage.getByRole("dialog").getByLabel("完工说明").fill("首次维修完成，请验收。");
  await workerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await workerContext.close();

  const reworkContext = await browser.newContext();
  const reworkPage = await reworkContext.newPage();
  await login(reworkPage, "zhangsan");
  await reworkPage.getByRole("link", { name: "报修服务" }).click();
  await reworkPage.getByRole("button", { name: new RegExp(marker) }).click();
  await reworkPage.getByRole("button", { name: "要求返工" }).click();
  await reworkPage.getByRole("dialog").getByLabel("返工原因").fill("灯具仍然闪烁，请重新检查。");
  await reworkPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await reworkContext.close();

  const finishContext = await browser.newContext();
  const finishPage = await finishContext.newPage();
  await login(finishPage, "repair_worker");
  await finishPage.getByRole("link", { name: "报修服务" }).click();
  await finishPage.getByRole("button", { name: new RegExp(marker) }).click();
  await finishPage.getByRole("button", { name: "提交返工完工" }).click();
  await finishPage.getByRole("dialog").getByLabel("返工完工说明").fill("已更换驱动并复测正常。");
  await finishPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await finishContext.close();

  const verifyContext = await browser.newContext();
  const verifyPage = await verifyContext.newPage();
  await login(verifyPage, "zhangsan");
  await verifyPage.getByRole("link", { name: "报修服务" }).click();
  await verifyPage.getByRole("button", { name: new RegExp(marker) }).click();
  await verifyPage.getByRole("button", { name: "验收通过" }).click();
  await verifyPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await verifyPage.getByRole("button", { name: "评价" }).click();
  await verifyPage.getByRole("dialog").getByLabel("评价内容").fill("维修完成，服务态度良好。");
  await verifyPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await expect(verifyPage.getByText("已完成", { exact: true }).last()).toBeVisible();
  await verifyContext.close();
});

test("公告跨角色完成创建、审核、二次确认发布", async ({ browser }) => {
  const title = `E2E发布闭环-${Date.now()}`;
  const customerContext = await browser.newContext();
  const customerPage = await customerContext.newPage();
  await login(customerPage, "customer_service");
  await customerPage.getByRole("link", { name: "社区公告" }).click();
  await customerPage.getByRole("button", { name: "新建草稿" }).click();
  await customerPage.getByLabel("标题").fill(title);
  await customerPage.getByLabel("正文").fill("今晚进行公共照明维护，请居民注意出行安全。");
  await customerPage.getByRole("button", { name: "保存草稿" }).click();
  await customerPage.getByRole("button", { name: "送审" }).click();
  await customerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await customerContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "社区公告" }).click();
  await managerPage.getByRole("button", { name: new RegExp(title) }).click();
  await managerPage.getByRole("button", { name: "批准" }).click();
  await managerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await managerPage.getByRole("button", { name: "确认发布" }).click();
  await expect(managerPage.getByRole("dialog").getByRole("button", { name: "二次确认并发布" })).toBeVisible();
  await managerPage.getByRole("dialog").getByRole("button", { name: "二次确认并发布" }).click();
  await expect(managerPage.getByRole("button", { name: new RegExp(`已发布.*${title}`) })).toBeVisible();

  const residentContext = await browser.newContext();
  const residentPage = await residentContext.newPage();
  await login(residentPage, "zhangsan");
  await residentPage.getByRole("link", { name: "社区公告" }).click();
  await expect(residentPage.getByRole("button", { name: new RegExp(`已发布.*${title}`) })).toBeVisible();
  await residentContext.close();

  await managerPage.getByRole("button", { name: "撤回", exact: true }).click();
  const withdrawDialog = managerPage.getByRole("dialog");
  await withdrawDialog.getByLabel("撤回原因").fill("维护计划已调整，撤回后重新发布。");
  await withdrawDialog.getByRole("button", { name: "确认操作" }).click();
  await expect(managerPage.getByRole("button", { name: new RegExp(`已撤回.*${title}`) })).toBeVisible();
  await managerContext.close();

  const hiddenContext = await browser.newContext();
  const hiddenPage = await hiddenContext.newPage();
  await login(hiddenPage, "zhangsan");
  await hiddenPage.getByRole("link", { name: "社区公告" }).click();
  await expect(hiddenPage.getByText(title)).toHaveCount(0);
  await hiddenContext.close();
});

test("公告驳回原因持久化且客服可看到驳回状态", async ({ browser }) => {
  const title = `E2E驳回公告-${Date.now()}`;
  const customerContext = await browser.newContext();
  const customerPage = await customerContext.newPage();
  await login(customerPage, "customer_service");
  await customerPage.getByRole("link", { name: "社区公告" }).click();
  await customerPage.getByRole("button", { name: "新建草稿" }).click();
  await customerPage.getByLabel("标题").fill(title);
  await customerPage.getByLabel("正文").fill("这是一条需要补充受众说明的测试公告。");
  await customerPage.getByRole("button", { name: "保存草稿" }).click();
  await customerPage.getByRole("button", { name: "送审" }).click();
  await customerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await customerContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "社区公告" }).click();
  await managerPage.getByRole("button", { name: new RegExp(title) }).click();
  await managerPage.locator(".detail-panel").getByRole("button", { name: "驳回", exact: true }).click();
  const rejectDialog = managerPage.getByRole("dialog");
  await rejectDialog.getByLabel("驳回原因").fill("请补充影响楼栋和具体生效时间。");
  await rejectDialog.getByRole("button", { name: "确认操作" }).click();
  await expect(managerPage.getByRole("button", { name: new RegExp(`已驳回.*${title}`) })).toBeVisible();
  await managerContext.close();

  const verifyContext = await browser.newContext();
  const verifyPage = await verifyContext.newPage();
  await login(verifyPage, "customer_service");
  await verifyPage.getByRole("link", { name: "社区公告" }).click();
  await expect(verifyPage.getByRole("button", { name: new RegExp(`已驳回.*${title}`) })).toBeVisible();
  await verifyContext.close();
});

test("巡检任务跨角色完成创建、分派、记录和复核", async ({ browser }) => {
  const title = `E2E巡检闭环-${Date.now()}`;
  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "巡检与事件" }).click();
  await managerPage.getByRole("button", { name: "新建巡检任务" }).click();
  const createDialog = managerPage.getByRole("dialog");
  await createDialog.getByLabel("任务标题").fill(title);
  await createDialog.getByLabel("任务说明").fill("检查消防通道、应急灯和灭火器状态。");
  await createDialog.getByLabel("路线点位（逗号分隔）").fill("1栋大厅,地下车库");
  await createDialog.getByRole("button", { name: "创建任务" }).click();
  await managerPage.getByRole("button", { name: "分派", exact: true }).click();
  const assignDialog = managerPage.getByRole("dialog");
  await assignDialog.getByLabel("安保人员").selectOption({ label: "安保老李" });
  await assignDialog.getByRole("button", { name: "确认操作" }).click();
  await managerContext.close();

  const guardContext = await browser.newContext();
  const guardPage = await guardContext.newPage();
  await login(guardPage, "security_guard");
  await guardPage.getByRole("link", { name: "巡检与事件" }).click();
  await guardPage.getByRole("button", { name: new RegExp(title) }).click();
  await guardPage.getByRole("button", { name: "开始巡检" }).click();
  await guardPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await guardPage.getByRole("button", { name: "提交记录" }).click();
  const recordDialog = guardPage.getByRole("dialog");
  await recordDialog.getByLabel("点位", { exact: true }).fill("1栋大厅");
  await recordDialog.getByLabel("记录内容").fill("消防通道畅通，设备状态正常。");
  await recordDialog.getByRole("button", { name: "确认操作" }).click();
  await guardContext.close();

  const reviewContext = await browser.newContext();
  const reviewPage = await reviewContext.newPage();
  await login(reviewPage, "manager");
  await reviewPage.getByRole("link", { name: "巡检与事件" }).click();
  await reviewPage.getByRole("button", { name: new RegExp(title) }).click();
  await reviewPage.getByRole("button", { name: "复核完成" }).click();
  await reviewPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await expect(reviewPage.getByRole("button", { name: new RegExp(`已完成.*${title}`) })).toBeVisible();
  await reviewContext.close();
});

test("安防事件支持退回后再次处置并完成复核", async ({ browser }) => {
  const location = `E2E安防退回-${Date.now()}`;
  const residentContext = await browser.newContext();
  const residentPage = await residentContext.newPage();
  await login(residentPage, "zhangsan");
  await residentPage.getByRole("link", { name: "巡检与事件" }).click();
  await residentPage.getByRole("button", { name: "人工上报事件" }).click();
  const createDialog = residentPage.getByRole("dialog");
  await createDialog.getByLabel("发生位置").fill(location);
  await createDialog.getByLabel("事件描述").fill("消防通道存在杂物，需要现场处置。");
  await createDialog.getByRole("button", { name: "核对并上报" }).click();
  await residentContext.close();

  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await login(managerPage, "manager");
  await managerPage.getByRole("link", { name: "巡检与事件" }).click();
  await managerPage.getByRole("button", { name: new RegExp(location) }).click();
  await managerPage.getByRole("button", { name: "分派处置" }).click();
  const assignDialog = managerPage.getByRole("dialog");
  await assignDialog.getByLabel("处置人员").selectOption({ label: "安保老李" });
  await assignDialog.getByRole("button", { name: "确认操作" }).click();

  const guardContext = await browser.newContext();
  const guardPage = await guardContext.newPage();
  await login(guardPage, "security_guard");
  await guardPage.getByRole("link", { name: "巡检与事件" }).click();
  await guardPage.getByRole("button", { name: new RegExp(location) }).click();
  await guardPage.getByRole("button", { name: "提交处置" }).click();
  await guardPage.getByRole("dialog").getByLabel("处置记录").fill("已清走部分杂物。");
  await guardPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();

  await managerPage.reload();
  await managerPage.getByRole("button", { name: new RegExp(location) }).click();
  await managerPage.getByRole("button", { name: "退回处置" }).click();
  await managerPage.getByRole("dialog").getByLabel("退回原因").fill("仍有杂物未清理，请完整处置并复核现场。");
  await managerPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();

  await guardPage.reload();
  await guardPage.getByRole("button", { name: new RegExp(location) }).click();
  await guardPage.getByRole("button", { name: "提交处置" }).click();
  await guardPage.getByRole("dialog").getByLabel("处置记录").fill("已全部清理并拍照复核现场。");
  await guardPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await guardContext.close();

  await managerPage.reload();
  await managerPage.getByRole("button", { name: new RegExp(location) }).click();
  await managerPage.getByRole("button", { name: "复核通过" }).click();
  await managerPage.getByRole("dialog").getByRole("button", { name: "确认复核通过" }).click();
  await expect(managerPage.getByRole("button", { name: new RegExp(`已完成.*${location}`) })).toBeVisible();
  await managerContext.close();
});

test("高风险安防事件必须先确认评级才能复核关闭", async ({ browser }) => {
  const location = `E2E高风险事件-${Date.now()}`;
  const reporterContext = await browser.newContext();
  const reporterPage = await reporterContext.newPage();
  await login(reporterPage, "manager");
  await reporterPage.getByRole("link", { name: "巡检与事件" }).click();
  await reporterPage.getByRole("button", { name: "人工上报事件" }).click();
  const createDialog = reporterPage.getByRole("dialog");
  await createDialog.getByLabel("事件类型").selectOption("GAS_LEAK");
  await createDialog.getByLabel("风险等级").selectOption("HIGH_RISK");
  await createDialog.getByLabel("发生位置").fill(location);
  await createDialog.getByLabel("事件描述").fill("疑似燃气泄漏，已隔离现场并通知值班人员。");
  await createDialog.getByRole("button", { name: "核对并上报" }).click();
  await reporterPage.getByRole("button", { name: "分派处置" }).click();
  const assignDialog = reporterPage.getByRole("dialog");
  await assignDialog.getByLabel("处置人员").selectOption({ label: "安保老李" });
  await assignDialog.getByRole("button", { name: "确认操作" }).click();

  const guardContext = await browser.newContext();
  const guardPage = await guardContext.newPage();
  await login(guardPage, "security_guard");
  await guardPage.getByRole("link", { name: "巡检与事件" }).click();
  await guardPage.getByRole("button", { name: new RegExp(location) }).click();
  await guardPage.getByRole("button", { name: "提交处置" }).click();
  await guardPage.getByRole("dialog").getByLabel("处置记录").fill("已关闭阀门、开窗通风并设置警戒区域。");
  await guardPage.getByRole("dialog").getByRole("button", { name: "确认操作" }).click();
  await guardContext.close();

  await reporterPage.reload();
  await reporterPage.getByRole("button", { name: new RegExp(location) }).click();
  await expect(reporterPage.getByRole("button", { name: "复核通过" })).toHaveCount(0);
  await reporterPage.getByRole("button", { name: "确认高风险评级" }).click();
  await reporterPage.getByRole("dialog").getByRole("button", { name: "确认高风险评级" }).click();
  await reporterPage.getByRole("button", { name: "复核通过" }).click();
  await reporterPage.getByRole("dialog").getByRole("button", { name: "确认复核通过" }).click();
  await expect(reporterPage.getByRole("button", { name: new RegExp(`已完成.*${location}`) })).toBeVisible();
  await reporterContext.close();
});

test("消息中心支持筛选、单条已读和全部已读", async ({ page }) => {
  await login(page, "zhangsan");
  await page.getByRole("link", { name: "消息中心" }).click();
  await expect(page.getByRole("heading", { name: "消息中心" })).toBeVisible();
  await page.getByLabel("阅读或投递状态").selectOption("UNREAD");
  const unreadCards = page.locator("article.entity-card.unread");
  if (await unreadCards.count()) {
    await unreadCards.first().getByRole("button", { name: "标为已读" }).click();
    await expect(unreadCards.first()).toHaveCount(0);
  }
  await page.getByLabel("阅读或投递状态").selectOption("");
  const existingUnreadTitles = await page.locator("article.entity-card.unread h3").allTextContents();
  const markAll = page.getByRole("button", { name: "全部标为已读" });
  if (await markAll.isEnabled()) {
    const responsePromise = page.waitForResponse(
      (response) => response.url().endsWith("/api/messages/read-all") && response.request().method() === "POST",
    );
    await markAll.click();
    expect((await responsePromise).ok()).toBeTruthy();
  }
  await page.getByLabel("阅读或投递状态").selectOption("UNREAD");
  for (const title of existingUnreadTitles) {
    await expect(page.getByRole("heading", { name: title, exact: true })).toHaveCount(0);
  }
});

test("管理工作台展示失败消息、重试上限和人工接管", async ({ page }) => {
  await login(page, "manager");
  await page.getByRole("link", { name: "管理工作台", exact: true }).click();
  const failedPanel = page.locator("section").filter({ has: page.getByRole("heading", { name: "需要人工接管的消息" }) });
  await expect(failedPanel.getByText(/已尝试 5\/5 次/).first()).toBeVisible();
  await expect(failedPanel.getByText(/待处理/).first()).toBeVisible();
  await expect(failedPanel.getByText(/备用联系：/).first()).not.toContainText("未配置");
});

test("Agent 取消后不创建工单且后端待确认状态被清除", async ({ page }) => {
  await login(page, "zhangsan");
  const headers = await authenticatedHeaders(page);
  const beforeResponse = await page.request.get("/api/work-orders?limit=100", { headers });
  expect(beforeResponse.ok()).toBeTruthy();
  const before = (await beforeResponse.json()).data.items.map((item: { id: string }) => item.id);

  await page.getByLabel("发送给社区智能体").fill("客厅电灯坏了，需要报修");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await completeRepairAppointment(page);
  await expect(page.getByRole("heading", { name: "确认提交这条报修吗？" })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  await expect(page.getByText("已取消，未执行任何操作。")).toBeVisible();

  const conversation = await page.evaluate(() => sessionStorage.getItem("property_agent_conversation_id"));
  const status = await page.request.get(`/api/agent/conversations/${conversation}`, { headers });
  expect(status.ok()).toBeTruthy();
  expect((await status.json()).data.pending_confirmation).toBeNull();
  const afterResponse = await page.request.get("/api/work-orders?limit=100", { headers });
  expect(afterResponse.ok()).toBeTruthy();
  const after = (await afterResponse.json()).data.items.map((item: { id: string }) => item.id);
  expect(after).toEqual(before);
});

test("Agent 待确认操作在刷新后恢复且确认只创建一个工单", async ({ page }) => {
  await login(page, "zhangsan");
  const headers = await authenticatedHeaders(page);
  const beforeResponse = await page.request.get("/api/work-orders?limit=100", { headers });
  expect(beforeResponse.ok()).toBeTruthy();
  const before = new Set(
    (await beforeResponse.json()).data.items.map((item: { id: string }) => item.id),
  );

  await page.getByLabel("发送给社区智能体").fill("客厅电灯坏了，需要报修");
  await page.getByRole("button", { name: "发送" }).click();
  await waitForAgent(page);
  await completeRepairAppointment(page);
  await expect(page.getByRole("heading", { name: "确认提交这条报修吗？" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "确认提交这条报修吗？" })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "确认提交" }).click();
  await expect(page.locator(".message.assistant").last()).toContainText("报修已提交");

  const afterResponse = await page.request.get("/api/work-orders?limit=100", { headers });
  expect(afterResponse.ok()).toBeTruthy();
  const created = (await afterResponse.json()).data.items.filter(
    (item: { id: string }) => !before.has(item.id),
  );
  expect(created).toHaveLength(1);
});
