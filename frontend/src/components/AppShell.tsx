import {
  Bell, Bot, Building2, ClipboardCheck, FileText, Gauge, LogOut,
  Menu, ReceiptText, Wrench, X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { workspaceFor, workspaceLabel, type WorkspaceKind } from "../ui/roles";
import { EnvironmentBadge } from "./EnvironmentBadge";
import { HouseSwitcher } from "./HouseSwitcher";

type NavItem = readonly [string, string, typeof Bot, string?];
type NavGroup = { title: string; items: NavItem[] };

const shared = {
  agent: ["/", "社区助手", Bot, "首页 / 智能体"] as NavItem,
  repairs: ["/repairs", "报修服务", Wrench] as NavItem,
  announcements: ["/announcements", "社区公告", FileText] as NavItem,
  inspection: ["/inspection", "巡检与事件", ClipboardCheck] as NavItem,
  messages: ["/messages", "消息中心", Bell] as NavItem,
};

function navigationFor(workspace: WorkspaceKind, roles: readonly string[]): NavGroup[] {
  if (workspace === "admin") return [
    { title: "运营概览", items: [shared.agent, ["/admin", "管理工作台", Gauge]] },
    { title: "业务调度", items: [shared.repairs, shared.announcements, shared.inspection] },
    { title: "协作", items: [shared.messages] },
  ];
  if (workspace === "maintenance") return [
    { title: "今日工作", items: [shared.agent, shared.repairs, ...(roles.includes("CUSTOMER_SERVICE") ? [shared.announcements] : []), ...(roles.some((role) => ["SECURITY_GUARD", "SECURITY_STAFF", "DUTY_STAFF"].includes(role)) ? [shared.inspection] : [])] },
    { title: "协作", items: [shared.messages] },
  ];
  return [
    { title: "我的社区", items: [shared.agent, shared.repairs, ["/billing", "账单费用", ReceiptText], shared.announcements, ["/inspection", "安全事件上报", ClipboardCheck, "巡检与事件"]] },
    { title: "服务消息", items: [shared.messages] },
  ];
}

export function AppShell() {
  const { session, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const workspace = workspaceFor(session?.actor.roles);
  const navigation = navigationFor(workspace, session?.actor.roles ?? []);
  const houseSelectionRequired = workspace === "resident" &&
    (session?.houses.length ?? 0) > 1 && !session?.current_house_id;
  return (
    <div className={`app-shell workspace-${workspace}`}>
      <EnvironmentBadge />
      <aside className={open ? "sidebar open" : "sidebar"}>
        <div className="brand"><span className="brand-mark"><Building2 /></span><div><b>栖邻</b><small>{workspaceLabel(workspace)}</small></div></div>
        <button className="mobile-close" aria-label="关闭菜单" onClick={() => setOpen(false)}><X /></button>
        <nav>
          {navigation.map((group) => <div className="nav-group" key={group.title}>
            <span className="nav-section">{group.title}</span>
            {group.items.map(([to, label, Icon, accessibleLabel]) => (
              <NavLink className={to === "/" ? "agent-nav" : undefined} aria-label={accessibleLabel} key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}>
                <Icon size={19} /><span>{label}</span>
              </NavLink>
            ))}
          </div>)}
        </nav>
        <div className="sidebar-foot"><Bot size={18} /><span>AI 只提供建议，业务结果以后端为准</span></div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <button className="menu-button" aria-label="打开菜单" onClick={() => setOpen(true)}><Menu /></button>
          <HouseSwitcher workspace={workspace} />
          <div className="profile"><span className="avatar">{session?.actor.display_name.slice(0, 1)}</span><div><b>{session?.actor.display_name}</b><small>{workspaceLabel(workspace)} · {session?.actor.community_name}</small></div></div>
          <button className="icon-button" aria-label="退出登录" onClick={logout}><LogOut size={19} /></button>
        </header>
        <div className="page">
          {houseSelectionRequired
            ? <section className="house-required" aria-labelledby="house-required-title">
                <span className="house-required-icon"><Building2 aria-hidden="true" /></span>
                <div>
                  <span className="eyebrow">访问房屋服务前</span>
                  <h1 id="house-required-title">请先选择要服务的房屋</h1>
                  <p id="house-selection-help">你绑定了多套房屋。请在页面上方选择一套，系统再加载对应的报修、账单和 Agent 会话。</p>
                </div>
              </section>
            : <Outlet />}
        </div>
      </main>
      {open && <button className="sidebar-scrim" aria-label="关闭菜单" onClick={() => setOpen(false)} />}
    </div>
  );
}
