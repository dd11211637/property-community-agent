import { AlertTriangle, CheckCircle2, CircleDashed, ServerCog } from "lucide-react";
import { apiRequest } from "../api/client";
import type { AdminDashboard } from "../api/contracts";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function AdminPage() {
  const dashboard = useApi(() => apiRequest<AdminDashboard>("/api/admin/dashboard"));
  if (dashboard.loading) return <Loading label="正在聚合管理数据" />;
  if (dashboard.error) return <ErrorState error={dashboard.error} retry={() => void dashboard.reload()} />;
  const data = dashboard.data;
  return <>
    <header className="page-heading"><div><span className="eyebrow">OPERATIONS</span><h1>管理工作台</h1><p>聚合待办、失败消息、高风险事件和接口健康状态。</p></div></header>
    <section className="metric-grid"><article><span>待处理事项</span><strong>{data?.pending_count ?? 0}</strong><CircleDashed /></article><article><span>失败消息</span><strong>{data?.failed_message_count ?? 0}</strong><AlertTriangle /></article><article><span>高风险事件</span><strong>{data?.high_risk_event_count ?? 0}</strong><AlertTriangle /></article></section>
    <section className="two-column">
      <div className="content-panel"><div className="panel-heading"><h2>待处理事项</h2><CircleDashed /></div>{!data?.pending_items.length ? <Empty /> : <div className="entity-list">{data.pending_items.map((item) => <article className="entity-card" key={item.id}><div className="entity-main"><span className="status">{item.status}</span><h3>{item.summary}</h3><small>{item.source} · {item.queue}</small></div></article>)}</div>}</div>
      <div className="content-panel"><div className="panel-heading"><h2>高风险事件</h2><AlertTriangle /></div>{!data?.high_risk_events.length ? <Empty /> : <div className="entity-list">{data.high_risk_events.map((event) => <article className="entity-card" key={event.id}><div className="entity-main"><span className="status failed">{event.risk_level} · {event.status}</span><h3>{event.business_no}</h3><small>{event.location}</small></div></article>)}</div>}</div>
    </section>
    <section className="content-panel admin-health"><div className="panel-heading"><h2>失败消息与人工接管</h2><AlertTriangle /></div>{!data?.failed_messages.length ? <Empty /> : <div className="entity-list">{data.failed_messages.map((message) => <article className="entity-card" key={message.id}><div className="entity-main"><span className="status failed">{message.status} · 重试 {message.retry_count}/{message.max_retry_count}</span><h3>{message.title}</h3><p>{message.body}</p><small>接管状态：{message.handover_status ?? "NOT_CREATED"} · 备用联系：{message.fallback_contact ?? "未配置"}</small></div></article>)}</div>}</section>
    <section className="content-panel admin-health"><div className="panel-heading"><h2>集成健康状态</h2><ServerCog /></div><div className="health-list">{Object.entries(data?.integration_health ?? {}).map(([name, health]) => <div key={name}>{health === "UP" || health === "CONFIGURED" ? <CheckCircle2 /> : <AlertTriangle />}<b>{name}</b><span>{health}</span></div>)}</div></section>
  </>;
}
