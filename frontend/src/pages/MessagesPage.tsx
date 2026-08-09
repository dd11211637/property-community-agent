import { Bell, CheckCheck } from "lucide-react";
import { apiRequest } from "../api/client";
import type { ListResult, MessageRecord } from "../api/contracts";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function MessagesPage() {
  const { data, error, loading, reload } = useApi(() => apiRequest<ListResult<MessageRecord>>("/api/messages"));
  return <><header className="page-heading"><div><span className="eyebrow">MESSAGE CENTER</span><h1>消息中心</h1><p>业务通知、发送失败和人工接管进度集中展示。</p></div><button className="button ghost" disabled><CheckCheck size={17} />全部标为已读</button></header><section className="content-panel">{loading ? <Loading /> : error ? <ErrorState error={error} retry={() => void reload()} /> : !data?.items.length ? <Empty title="没有新消息" /> : <div className="entity-list">{data.items.map((item) => <article className="entity-card" key={item.id}><span className="entity-icon"><Bell /></span><div className="entity-main"><span className={`status ${item.status.toLowerCase()}`}>{item.status}</span><h3>{item.title}</h3><p>{item.business_type} · {new Date(item.created_at).toLocaleString("zh-CN")}</p></div></article>)}</div>}</section></>;
}
