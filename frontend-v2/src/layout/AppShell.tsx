import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Bot,
  Building2,
  ChevronDown,
  CircleGauge,
  Home,
  LogOut,
  Mail,
  Menu as MenuIcon,
  Megaphone,
  ReceiptText,
  ShieldCheck,
  Wrench,
  Settings,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { routeCapabilities } from "../app/routeConfig";
import { useRuntimeMode } from "../app/runtimeModeDefinition";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { Drawer, Menu, MenuItem, Tooltip } from "../shared/overlays";
import { Button } from "../shared/ui";
import styles from "../styles/app.module.css";

const icons: Record<string, React.ReactNode> = {
  "/": <Home size={19} />,
  "/agent": <Bot size={19} />,
  "/repairs": <Wrench size={19} />,
  "/field": <Wrench size={19} />,
  "/billing": <ReceiptText size={19} />,
  "/community": <Megaphone size={19} />,
  "/operations": <ShieldCheck size={19} />,
  "/messages": <Mail size={19} />,
  "/admin": <CircleGauge size={19} />,
  "/settings/ai-memory": <Settings size={19} />,
};

function Navigation() {
  const { session } = useSession();
  if (session.status !== "authenticated") return null;
  const resident = hasCapability(session.actor.roles, "resident-experience");
  const operations = hasCapability(session.actor.roles, "operations");
  const fieldService = hasCapability(session.actor.roles, "field-service");
  const admin = hasCapability(session.actor.roles, "admin");
  const routes = routeCapabilities.filter((route) => {
    if (route.navigation === "none") return false;
    if (route.path === "/") return true;
    if (route.navigation === "resident" && !resident) return false;
    if (route.navigation === "operations" && !operations) return false;
    if (route.navigation === "field-service" && !fieldService) return false;
    if (route.navigation === "both" && !resident && !operations && !fieldService)
      return false;
    if (route.requiredCapability === "admin" && !admin) return false;
    return true;
  });
  return (
    <nav className={styles.nav} aria-label="主导航">
      {routes.map((route) => (
        <NavLink
          key={route.path}
          to={route.path}
          end={route.path === "/"}
          className={({ isActive }) =>
            `${styles.navLink} ${isActive ? styles.active : ""}`
          }
        >
          {icons[route.path]}
          <span>
            {route.path === "/" && operations ? "工作台" : route.label}
          </span>
        </NavLink>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <NavLink to="/" className={styles.brand}>
      <span className={styles.brandMark}>
        <Building2 size={21} />
      </span>
      <span>
        <strong>邻里方舟</strong>
        <small>社区服务中心</small>
      </span>
    </NavLink>
  );
}

function SidebarContent() {
  const mode = useRuntimeMode();
  return (
    <>
      <Brand />
      <Navigation />
      <div className={styles.sidebarFoot}>
        <p>{mode === "demo" ? "设计预览" : "本地社区服务"}</p>
        <span>
          {mode === "demo"
            ? "Demo 数据 · 非生产"
            : "业务办理与生活助理"}
        </span>
      </div>
    </>
  );
}

export function AppShell() {
  const { session, transitioning, selectionError, selectHouse, signOut } =
    useSession();
  const mode = useRuntimeMode();
  if (session.status !== "authenticated") return null;
  const currentHouse = session.houses.find(
    (house) => house.id === session.currentHouseId,
  );
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <SidebarContent />
      </aside>
      <main className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.mobileMenu}>
            <Drawer
              title="导航"
              trigger={
                <Button iconOnly tone="ghost" aria-label="打开导航">
                  <MenuIcon />
                </Button>
              }
            >
              <SidebarContent />
            </Drawer>
          </div>
          <div className={styles.houseControl}>
            <label>
              <span className="sr-only">当前房屋</span>
              <select
                className={styles.house}
                aria-label="当前房屋"
                value={session.currentHouseId ?? ""}
                disabled={transitioning || session.houses.length === 0}
                onChange={(event) => void selectHouse(event.target.value)}
              >
                <option value="">
                  {session.houses.length === 0 ? "无可用房屋" : "选择当前房屋"}
                </option>
                {session.houses.map((house) => (
                  <option key={house.id} value={house.id}>
                    {house.label}
                  </option>
                ))}
              </select>
            </label>
            {selectionError ? (
              <span className={styles.houseError} role="alert">
                {selectionError}
              </span>
            ) : null}
          </div>
          <Menu
            trigger={
              <Button tone="ghost" aria-label="打开用户菜单">
                <span className={styles.user}>
                  <span className={styles.userCopy}>
                    <strong>{session.actor.displayName}</strong>
                    <small>
                      {currentHouse?.label ?? session.actor.communityName}
                    </small>
                  </span>
                  <span className={styles.avatar}>
                    {session.actor.displayName.slice(0, 1)}
                  </span>
                  <ChevronDown size={16} />
                </span>
              </Button>
            }
          >
            <DropdownMenu.Label className={styles.accountLabel}>
              当前身份
            </DropdownMenu.Label>
            <MenuItem>
              <Bot size={16} />
              {session.actor.displayName}
            </MenuItem>
            <MenuItem>
              <Building2 size={16} />
              {session.actor.communityName}
            </MenuItem>
            <MenuItem>
              <ShieldCheck size={16} />
              {session.actor.roles.length ? "已认证社区成员" : "普通用户"}
            </MenuItem>
            <DropdownMenu.Separator />
            <MenuItem onSelect={() => void signOut()}>
              <LogOut size={16} />
              {mode === "demo" ? "退出预览" : "退出登录"}
            </MenuItem>
          </Menu>
        </header>
        {transitioning ? (
          <div className={styles.transition} role="status">
            正在验证并切换房屋上下文…
          </div>
        ) : null}
        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

export function MobileHelp() {
  return (
    <Tooltip label="Agent 能力说明">
      <Button iconOnly tone="ghost" aria-label="Agent 能力说明">
        <Bot />
      </Button>
    </Tooltip>
  );
}
