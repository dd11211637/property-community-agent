import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Bot, Building2, ChevronDown, CircleGauge, Home, LogOut, Mail, Menu as MenuIcon, Megaphone, ReceiptText, ShieldCheck, Wrench } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { routeCapabilities } from "../app/routeConfig";
import styles from "../styles/app.module.css";
import { Button } from "../shared/ui";
import { Drawer, Menu, MenuItem, Tooltip } from "../shared/overlays";

const icons: Record<string, React.ReactNode> = {
  "/": <Home size={19} />, "/repairs": <Wrench size={19} />, "/billing": <ReceiptText size={19} />,
  "/community": <Megaphone size={19} />, "/operations": <ShieldCheck size={19} />, "/messages": <Mail size={19} />, "/admin": <CircleGauge size={19} />,
};

function Navigation() {
  const { session } = useSession();
  if (session.status !== "authenticated") return null;
  const operations = hasCapability(session.actor.roles, "operations");
  const admin = hasCapability(session.actor.roles, "admin");
  const mode = operations ? "operations" : "resident";
  const routes = routeCapabilities.filter((route) => {
    if (route.navigation === "none") return false;
    if (route.navigation !== "both" && route.navigation !== mode) return false;
    if (route.requiredCapability === "operations" && !operations) return false;
    if (route.requiredCapability === "admin" && !admin) return false;
    return true;
  });
  return <nav className={styles.nav} aria-label="主导航">{routes.map((route) => <NavLink key={route.path} to={route.path} end={route.path === "/"} className={({ isActive }) => `${styles.navLink} ${isActive ? styles.active : ""}`}>{icons[route.path]}<span>{route.path === "/" && operations ? "工作台" : route.label}</span></NavLink>)}</nav>;
}

function Brand() {
  return <NavLink to="/" className={styles.brand}><span className={styles.brandMark}><Building2 size={21} /></span><span><strong>邻里方舟</strong><small>Agentic Community</small></span></NavLink>;
}

function SidebarContent() {
  return <><Brand /><Navigation /><div className={styles.sidebarFoot}><p>服务运行平稳</p><span>Skeleton 展示环境 · 非生产</span></div></>;
}

export function AppShell() {
  const { session, transitioning, selectHouse, signOut } = useSession();
  if (session.status !== "authenticated") return null;
  const currentHouse = session.houses.find((house) => house.id === session.currentHouseId);
  return <div className={styles.shell}><aside className={styles.sidebar}><SidebarContent /></aside><main className={styles.main}><header className={styles.topbar}><div className={styles.mobileMenu}><Drawer title="导航" trigger={<Button iconOnly tone="ghost" aria-label="打开导航"><MenuIcon /></Button>}><SidebarContent /></Drawer></div><label><span className="sr-only">当前房屋</span><select className={styles.house} aria-label="当前房屋" value={session.currentHouseId ?? ""} onChange={(event) => void selectHouse(event.target.value)}><option value="" disabled>请选择房屋</option>{session.houses.map((house) => <option key={house.id} value={house.id}>{house.label}</option>)}</select></label><Menu trigger={<Button tone="ghost" aria-label="打开用户菜单"><span className={styles.user}><span className={styles.userCopy}><strong>{session.actor.displayName}</strong><small>{currentHouse?.label ?? session.actor.communityName}</small></span><span className={styles.avatar}>{session.actor.displayName.slice(0, 1)}</span><ChevronDown size={16} /></span></Button>}><DropdownMenu.Label className={styles.navLink}>当前身份</DropdownMenu.Label><MenuItem><Bot size={16} />{session.actor.roles.join(" · ")}</MenuItem><DropdownMenu.Separator /><MenuItem onSelect={signOut}><LogOut size={16} />退出预览</MenuItem></Menu></header>{transitioning ? <div className={styles.transition} role="status">正在切换房屋上下文…</div> : null}<div className={styles.content}><Outlet /></div></main></div>;
}

export function MobileHelp() {
  return <Tooltip label="Agent 能力说明"><Button iconOnly tone="ghost" aria-label="Agent 能力说明"><Bot /></Button></Tooltip>;
}
