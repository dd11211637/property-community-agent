import { expect, test, type Browser, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const stage = process.env.VISUAL_EVIDENCE_STAGE ?? "after";
const outputRoot = path.resolve(process.cwd(), `../docs/frontend/screenshots/${stage}`);

async function signIn(page: Page, account: string) {
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("123456");
  await page.getByRole("button", { name: /登录/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.waitForLoadState("networkidle");
}

async function capture(page: Page, name: string) {
  await page.screenshot({
    path: path.join(outputRoot, `${name}.png`),
    fullPage: true,
  });
}

async function rolePage(browser: Browser, account: string) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await signIn(page, account);
  return { context, page };
}

test.beforeAll(async () => {
  await mkdir(outputRoot, { recursive: true });
});

test("capture release visual evidence", async ({ browser }) => {
  const loginContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const loginPage = await loginContext.newPage();
  await loginPage.goto("/login");
  await capture(loginPage, "login");
  await loginContext.close();

  const resident = await rolePage(browser, "zhangsan");
  await capture(resident.page, "resident-home");
  for (const [route, name] of [
    ["/repairs", "resident-repairs"],
    ["/billing", "resident-billing"],
    ["/announcements", "resident-announcements"],
    ["/messages", "resident-agent-messages"],
  ] as const) {
    await resident.page.goto(route);
    await resident.page.waitForLoadState("networkidle");
    await capture(resident.page, name);
  }
  await resident.context.close();

  const maintenance = await rolePage(browser, "repair_worker");
  await capture(maintenance.page, "maintenance-home");
  await maintenance.page.goto("/repairs");
  await maintenance.page.waitForLoadState("networkidle");
  const firstRepair = maintenance.page.locator(".entity-button, [data-testid='repair-item']").first();
  if (await firstRepair.isVisible()) {
    await firstRepair.click();
    await maintenance.page.waitForTimeout(250);
  }
  await capture(maintenance.page, "maintenance-repair-detail");
  await maintenance.context.close();

  const admin = await rolePage(browser, "manager");
  await capture(admin.page, "admin-home");
  for (const [route, name] of [
    ["/announcements", "admin-announcements"],
    ["/inspection", "admin-inspection"],
    ["/admin", "admin-workspace"],
  ] as const) {
    await admin.page.goto(route);
    await admin.page.waitForLoadState("networkidle");
    await capture(admin.page, name);
  }
  await admin.context.close();
});
