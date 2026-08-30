import { AlertTriangle, ArrowRight, Bell, ClipboardCheck, ReceiptText, Wrench } from "lucide-react";
import { Link } from "react-router-dom";
import { apiRequest } from "../api/client";
import type { AdminDashboard, Announcement, Bill, ListResult, Session, WorkOrder } from "../api/contracts";
import { useApi } from "../hooks/useApi";
import { businessReference, displayLabel, displayMoney } from "../ui/display";
import { workspaceFor } from "../ui/roles";
import { ErrorState, Loading } from "./AsyncState";
import { StatusBadge } from "./StatusBadge";

function readSession(): Session | null {
  try {
    const stored = sessionStorage.getItem("property_agent_session");
    return stored ? JSON.parse(stored) as Session : null;
  } catch {
    return null;
  }
}

function ResidentOverview() {
  const repairs = useApi(() => apiRequest<ListResult<WorkOrder>>("/api/work-orders"));
  const bills = useApi(() => apiRequest<Bill[]>("/api/billing/bills"));
  const announcements = useApi(() => apiRequest<ListResult<Announcement>>("/api/announcements"));
  if (repairs.loading || bills.loading || announcements.loading) return <Loading label="正在整理你的社区服务" />;
  const error = repairs.error ?? bills.error ?? announcements.error;
  if (error) return <ErrorState error={error} retry={() => void Promise.all([repairs.reload(), bills.reload(), announcements.reload()])} />;
  const repair = repairs.data?.items[0];
  const bill = bills.data?.[0];
  const announcement = announcements.data?.items[0];
  return <section className="role-overview resident-overview">
    <div className="overview-lead"><span>我的社区生活</span><h1>今天，家里有什么需要留意？</h1><p>服务进度、费用和社区消息已经按重要程度整理好。</p><Link className="button primary" to="/repairs">发起报修<ArrowRight aria-hidden="true" /></Link></div>
    <div className="resident-snapshot">
      <Link to="/repairs"><Wrench aria-hidden="true" /><span>最近报修</span><b>{repair?.location ?? "暂无进行中的报修"}</b>{repair && <StatusBadge value={repair.status} />}</Link>
      <Link to="/billing"><ReceiptText aria-hidden="true" /><span>本期账单</span><b>{bill ? `¥ ${displayMoney(bill.total_amount)}` : "暂无账单"}</b>{bill && <StatusBadge value={bill.status} />}</Link>
      <Link to="/announcements"><Bell aria-hidden="true" /><span>最新公告</span><b>{announcement?.title ?? "暂无新公告"}</b><small>{announcement ? displayLabel(announcement.category, "社区通知") : "有新消息时会出现在这里"}</small></Link>
    </div>
  </section>;
}

function MaintenanceOverview() {
  const work = useApi(() => apiRequest<ListResult<WorkOrder>>("/api/work-orders"));
  if (work.loading) return <Loading label="正在整理今日任务" />;
  if (work.error) return <ErrorState error={work.error} retry={() => void work.reload()} />;
  const items = work.data?.items ?? [];
  const urgent = items.filter((item) => item.urgency === "URGENT").length;
  const actionable = items.filter((item) => item.available_actions?.length).slice(0, 3);
  return <section className="role-overview maintenance-overview">
    <div className="overview-lead"><span>现场工作</span><h1>今天的维修任务</h1><p>{items.length ? `共有 ${items.length} 个相关工单，${urgent} 个需要优先关注。` : "当前没有待处理工单。"}</p><Link className="button primary" to="/repairs">进入任务队列<ArrowRight aria-hidden="true" /></Link></div>
    <div className="task-focus"><div className="focus-heading"><ClipboardCheck aria-hidden="true" /><div><span>接下来处理</span><b>{actionable.length ? "按优先级继续任务" : "等待新的任务分配"}</b></div></div>{actionable.map((item) => <Link to="/repairs" key={item.id} data-testid="repair-item"><div><b>{businessReference(item.business_no, item.location)}</b><span>{item.location}</span></div><StatusBadge value={item.status} /></Link>)}</div>
  </section>;
}

function AdminOverview() {
  const dashboard = useApi(() => apiRequest<AdminDashboard>("/api/admin/dashboard"));
  if (dashboard.loading) return <Loading label="正在汇总运营事项" />;
  if (dashboard.error) return <ErrorState error={dashboard.error} retry={() => void dashboard.reload()} />;
  const data = dashboard.data;
  return <section className="role-overview admin-overview">
    <div className="overview-lead"><span>社区运营</span><h1>需要你处理的事项</h1><p>优先处理风险、失败通知和等待决策的业务。</p><Link className="button primary" to="/admin">打开管理工作台<ArrowRight aria-hidden="true" /></Link></div>
    <div className="attention-board"><article><ClipboardCheck aria-hidden="true" /><span>待处理</span><strong>{data?.pending_count ?? 0}</strong><small>等待决策或流转</small></article><article className="danger"><Bell aria-hidden="true" /><span>送达失败</span><strong>{data?.failed_message_count ?? 0}</strong><small>需要人工接管</small></article><article className="danger"><AlertTriangle aria-hidden="true" /><span>高风险事件</span><strong>{data?.high_risk_event_count ?? 0}</strong><small>需要及时核查</small></article></div>
  </section>;
}

export function RoleOverview() {
  const session = readSession();
  if (!session) return null;
  const workspace = workspaceFor(session.actor.roles);
  if (workspace === "admin") return <AdminOverview />;
  if (workspace === "maintenance") return <MaintenanceOverview />;
  return <ResidentOverview />;
}
