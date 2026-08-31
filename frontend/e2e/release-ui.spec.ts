import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, account: string) {
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("123456");
  await page.getByRole("button", { name: /登录/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.waitForLoadState("networkidle");
}

test("关键视口无横向溢出且移动导航可操作", async ({ browser }) => {
  for (const width of [375, 768, 1024, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    await signIn(page, "zhangsan");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${width}px viewport overflow`).toBeLessThanOrEqual(1);
    if (width <= 900) {
      await page.getByRole("button", { name: "打开菜单" }).click();
      await expect(page.getByRole("navigation")).toBeVisible();
      await page.getByRole("button", { name: "关闭菜单" }).first().click();
    }
    await context.close();
  }
});

test("导航和首页按角色呈现且不暴露内部标识", async ({ browser }) => {
  for (const [account, heading, expectedLink, forbiddenLink] of [
    ["zhangsan", "今天，家里有什么需要留意？", "账单费用", "管理工作台"],
    ["repair_worker", "今天的维修任务", "报修服务", "账单费用"],
    ["manager", "需要你处理的事项", "管理工作台", "账单费用"],
  ] as const) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await signIn(page, account);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    await expect(page.getByRole("link", { name: expectedLink, exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: forbiddenLink, exact: true })).toHaveCount(0);
    const primaryText = await page.locator(".role-overview").innerText();
    expect(primaryText).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i);
    expect(primaryText).not.toMatch(/\b(?:OPEN|ASSIGNED|IN_PROGRESS|COMPLETED|NOT_CREATED|DEGRADED)\b/);
    await context.close();
  }
});

test("系统减少动态效果偏好得到响应", async ({ browser }) => {
  const context = await browser.newContext({
    reducedMotion: "reduce",
    viewport: { width: 1024, height: 900 },
  });
  const page = await context.newPage();
  await signIn(page, "zhangsan");
  await expect.poll(() => page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  const transitionDuration = await page.locator(".button").first().evaluate(
    (element) => getComputedStyle(element).transitionDuration,
  );
  expect(Number.parseFloat(transitionDuration)).toBeLessThanOrEqual(0.00001);
  await context.close();
});
