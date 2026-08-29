import { expect, test, type Page, type Route } from "@playwright/test";

type Account = "resident" | "manager" | "multi" | "expired";
const identities = {
  resident: { actor_id: "actor-resident", display_name: "真实居民", roles: ["RESIDENT"], house_ids: ["house-a"], current_house_id: "house-a" },
  manager: { actor_id: "actor-manager", display_name: "真实经理", roles: ["MANAGER"], house_ids: [], current_house_id: null },
  multi: { actor_id: "actor-multi", display_name: "多房居民", roles: ["RESIDENT"], house_ids: ["house-a", "house-b"], current_house_id: null },
  expired: { actor_id: "actor-expired", display_name: "过期用户", roles: ["RESIDENT"], house_ids: ["house-a"], current_house_id: "house-a" },
} as const;

async function installAuthTransport(page: Page) {
  await page.route("**/api/auth/login", async (route: Route) => {
    const body = route.request().postDataJSON() as { username: Account };
    const identity = identities[body.username];
    if (!identity) return route.fulfill({ status: 401, json: { detail: "invalid" } });
    return route.fulfill({ status: 200, json: { access_token: `token-${body.username}`, token_type: "bearer", community_id: "community-a", community_name: "真实社区", ...identity } });
  });
  await page.route("**/api/auth/house", async (route: Route) => {
    const authorization = route.request().headers().authorization;
    if (authorization === "Bearer token-expired") return route.fulfill({ status: 401, json: { detail: "expired" } });
    const body = route.request().postDataJSON() as { house_id: string };
    if (body.house_id === "house-b") return route.fulfill({ status: 200, json: { house_id: "house-b", building: "6 栋", unit: "1 单元", room_no: "802" } });
    return route.fulfill({ status: 200, json: { house_id: "house-a", building: "1 栋", unit: "2 单元", room_no: "1203" } });
  });
}

async function login(page: Page, account: Account) {
  await installAuthTransport(page);
  await page.goto("/login");
  await page.getByLabel("账号").fill(account);
  await page.getByLabel("密码").fill("secret");
  await page.getByRole("button", { name: "登录" }).click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("resident real runtime authenticates without exposing Demo business records", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page, "resident");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "你好，真实居民" })).toBeVisible();
  await expect(page.getByLabel("当前房屋")).toContainText("1 栋 · 2 单元 · 1203");
  await page.getByRole("link", { name: "账单" }).click();
  await expect(page.getByRole("heading", { name: "业务页面尚未迁移" })).toBeVisible();
  await expect(page.getByText("卫生间顶部渗水")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("manager gets deterministic operations and admin navigation", async ({ page }) => {
  await login(page, "manager");
  await expect(page.getByRole("heading", { name: "欢迎回来，真实经理" })).toBeVisible();
  await expect(page.getByRole("link", { name: "账单" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "运营" })).toBeVisible();
  await page.getByRole("link", { name: "管理" }).click();
  await expect(page.getByRole("heading", { name: "业务页面尚未迁移" })).toBeVisible();
});

test("multi-house actor must choose and only then receives real display metadata", async ({ page }) => {
  await login(page, "multi");
  await expect(page.getByText("请选择当前房屋后再进入需要房屋作用域的服务。")).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "登录社区工作台" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("property_agent_v2_session"))).toBeNull();
});

test("authenticated 401 during initial house resolution returns to login", async ({ page }) => {
  await login(page, "expired");
  await expect(page.getByRole("heading", { name: "登录社区工作台" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.length)).toBe(0);
});

test("mobile navigation, account controls and house selector are keyboard operable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "multi");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("dialog").getByRole("link", { name: "社区" })).toBeVisible();
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

test("dedicated Demo entry remains an explicitly labeled design preview", async ({ page }) => {
  await page.goto("/demo.html#/login");
  await expect(page.getByRole("heading", { name: "进入产品预览" })).toBeVisible();
  await page.getByLabel("预览身份").fill("resident");
  await page.getByLabel("预览口令").fill("preview");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.getByText("Demo 数据 · 非生产")).toBeVisible();
  await expect(page.getByRole("heading", { name: /生活里的小事/ })).toBeVisible();
});
