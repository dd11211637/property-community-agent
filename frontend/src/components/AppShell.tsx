import {
  Bell, Bot, Building2, ClipboardCheck, FileText, Gauge, Home, LogOut,
  Menu, ReceiptText, Settings, Wrench, X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EnvironmentBadge } from "./EnvironmentBadge";

const navigation = [
  ["/", "首页 / 智能体", Home],
  ["/repairs", "报修服务", Wrench],
  ["/announcements", "社区公告", FileText],
  ["/billing", "账单费用", ReceiptText],
  ["/inspection", "巡检与事件", ClipboardCheck],
  ["/messages", "消息中心", Bell],
  ["/admin", "管理工作台", Gauge],
] as const;

export function AppShell() {
  const { session, logout, selectHouse } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell">
      <EnvironmentBadge />
      <aside className={open ? "sidebar open" : "sidebar"}>
        <div className="brand"><span className="brand-mark"><Building2 /></span><div><b>栖邻</b><small>社区服务中枢</small></div></div>
        <button className="mobile-close" aria-label="关闭菜单" onClick={() => setOpen(false)}><X /></button>
        <nav>
          {navigation.map(([to, label, Icon]) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}>
              <Icon size={19} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot"><Bot size={18} /><span>AI 只提供建议，业务结果以后端为准</span></div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <button className="menu-button" aria-label="打开菜单" onClick={() => setOpen(true)}><Menu /></button>
          <div className="house-picker">
            <small>当前房屋</small>
            <select
              aria-label="当前房屋"
              value={session?.current_house_id ?? ""}
              onChange={(event) => {
                const house = session?.houses.find((item) => item.id === event.target.value);
                if (house) void selectHouse(house);
              }}
            >
              {!session?.current_house_id && <option value="">请选择房屋</option>}
              {session?.houses.map((house) => <option value={house.id} key={house.id}>{house.label}</option>)}
            </select>
          </div>
          <div className="profile"><span className="avatar">{session?.actor.display_name.slice(0, 1)}</span><div><b>{session?.actor.display_name}</b><small>{session?.actor.community_name}</small></div></div>
          <button className="icon-button" aria-label="设置"><Settings size={19} /></button>
          <button className="icon-button" aria-label="退出登录" onClick={logout}><LogOut size={19} /></button>
        </header>
        <div className="page"><Outlet /></div>
      </main>
      {open && <button className="sidebar-scrim" aria-label="关闭菜单" onClick={() => setOpen(false)} />}
    </div>
  );
}
