import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, account: "resident" | "manager") {
  await page.goto("/login");
  await page.getByLabel("预览身份").fill(account);
  await page.getByLabel("预览口令").fill("preview");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("resident desktop showcase exposes core tasks", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page, "resident");
  await expect(page.getByRole("heading", { name: /生活里的小事/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "账单" })).toBeVisible();
  await expect(page.getByRole("link", { name: "管理" })).toHaveCount(0);
  await page.getByRole("link", { name: "报修" }).click();
  await expect(page.getByRole("heading", { name: "报修与工单" })).toBeVisible();
  await page.getByRole("button", { name: "新建报修" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expectNoHorizontalOverflow(page);
});

test("operations desktop reaches three-pane workspace, operations and admin", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1000 });
  await login(page, "manager");
  await expect(page.getByRole("heading", { name: /今天，从最重要的事项开始/ })).toBeVisible();
  await expect(page.getByText("最近会话")).toBeVisible();
  await expect(page.getByText("当前上下文")).toBeVisible();
  await page.getByRole("link", { name: "运营" }).click();
  await expect(page.getByRole("heading", { name: "运营态势" })).toBeVisible();
  await page.getByRole("link", { name: "管理" }).click();
  await expect(page.getByRole("heading", { name: "管理与服务状态" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("tablet operations layout keeps primary workflows accessible", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 1180 });
  await login(page, "manager");
  await page.getByRole("link", { name: "消息" }).click();
  await expect(page.getByRole("heading", { name: "消息与会话" })).toBeVisible();
  await expect(page.getByText("关联上下文")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("mobile resident navigation and cards remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "resident");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("link", { name: "社区" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("发送给社区智能体")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("reduced-motion preference preserves the showcase", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await login(page, "resident");
  await expect(page.getByRole("heading", { name: /生活里的小事/ })).toBeVisible();
  await page.getByRole("link", { name: "社区" }).click();
  await expect(page.getByRole("heading", { name: "社区动态" })).toBeVisible();
});
