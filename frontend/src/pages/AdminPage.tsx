import { AlertTriangle, CheckCircle2, CircleDashed, ServerCog } from "lucide-react";
import { apiRequest } from "../api/client";
import type { AdminDashboard } from "../api/contracts";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";
import { businessReference, displayIntegration, displayLabel } from "../ui/display";
import { StatusBadge } from "../components/StatusBadge";

export function AdminPage() {
  const dashboard = useApi(() => apiRequest<AdminDashboard>("/api/admin/dashboard"));
  if (dashboard.loading) return <Loading label="正在聚合管理数据" />;
  if (dashboard.error) return <ErrorState error={dashboard.error} retry={() => void dashboard.reload()} />;
  const data = dashboard.data;
  return <>
    <header className="page-heading"><div><span className="eyebrow">运营决策</span><h1>管理工作台</h1><p>先处理风险与异常，再查看支撑服务状态。</p></div></header>
    <section className="metric-grid"><article><span>待处理事项</span><strong>{data?.pending_count ?? 0}</strong><CircleDashed /></article><article><span>失败消息</span><strong>{data?.failed_message_count ?? 0}</strong><AlertTriangle /></article><article><span>高风险事件</span><strong>{data?.high_risk_event_count ?? 0}</strong><AlertTriangle /></article></section>
    <section className="two-column">
      <div className="content-panel decision-panel"><div className="panel-heading"><h2>等待决策</h2><CircleDashed /></div>{!data?.pending_items.length ? <Empty title="暂无待决策事项" /> : <div className="entity-list">{data.pending_items.map((item) => <article className="entity-card" key={item.id}><div className="entity-main"><StatusBadge value={item.status} /><h3>{item.summary}</h3><small>运营事项 · 请按业务流程继续处理</small></div></article>)}</div>}</div>
      <div className="content-panel decision-panel danger-panel"><div className="panel-heading"><h2>高风险事件</h2><AlertTriangle /></div>{!data?.high_risk_events.length ? <Empty title="暂无高风险事件" /> : <div className="entity-list">{data.high_risk_events.map((event) => <article className="entity-card" key={event.id}><div className="entity-main"><StatusBadge value={event.risk_level} /><h3>{businessReference(event.business_no, "高风险事件")}</h3><small>{event.location} · {displayLabel(event.status)}</small></div></article>)}</div>}</div>
    </section>
    <section className="content-panel admin-health"><div className="panel-heading"><h2>需要人工接管的消息</h2><AlertTriangle /></div>{!data?.failed_messages.length ? <Empty title="暂无送达失败消息" /> : <div className="entity-list">{data.failed_messages.map((message) => <article className="entity-card" key={message.id}><div className="entity-main"><StatusBadge value={message.status} /><h3>{message.title}</h3><p>{message.body}</p><small>已尝试 {message.retry_count}/{message.max_retry_count} 次 · {displayLabel(message.handover_status, "等待人工接管")} · 备用联系：{message.fallback_contact ?? "未配置"}</small></div></article>)}</div>}</section>
    <section className="content-panel admin-health health-evidence"><div className="panel-heading"><h2>服务支撑状态</h2><ServerCog /></div><div className="health-list">{Object.entries(data?.integration_health ?? {}).map(([name, health]) => <div key={name}>{health === "UP" || health === "CONFIGURED" ? <CheckCircle2 /> : <AlertTriangle />}<b>{displayIntegration(name)}</b><span>{displayLabel(health, "待检测")}</span></div>)}</div></section>
  </>;
}
