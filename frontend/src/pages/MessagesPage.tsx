import { useState } from "react";
import { Bell, Check, CheckCheck } from "lucide-react";
import { apiRequest, createIdempotencyKey, queryString } from "../api/client";
import type { ListResult, MessageRecord } from "../api/contracts";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function MessagesPage() {
  const [status, setStatus] = useState("");
  const [businessType, setBusinessType] = useState("");
  const [operationError, setOperationError] = useState<unknown>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const query = queryString({ status, business_type: businessType });
  const messages = useApi(
    () => apiRequest<ListResult<MessageRecord>>(`/api/messages${query}`),
    query,
  );
  const unreadCount = messages.data?.items.filter((item) => !item.is_read).length ?? 0;

  async function markRead(messageId: string) {
    setOperationError(null);
    setPendingId(messageId);
    try {
      await apiRequest(`/api/messages/${messageId}/read`, {
        method: "POST",
        idempotencyKey: createIdempotencyKey("message-read"),
      });
      await messages.reload();
    } catch (error) {
      setOperationError(error);
    } finally {
      setPendingId(null);
    }
  }

  async function markAllRead() {
    setOperationError(null);
    setPendingId("all");
    try {
      await apiRequest("/api/messages/read-all", {
        method: "POST",
        idempotencyKey: createIdempotencyKey("message-read-all"),
      });
      await messages.reload();
    } catch (error) {
      setOperationError(error);
    } finally {
      setPendingId(null);
    }
  }

  return <>
    <header className="page-heading">
      <div><span className="eyebrow">MESSAGE CENTER</span><h1>消息中心</h1><p>业务通知、发送失败和人工接管进度集中展示。</p></div>
      <button className="button ghost" disabled={!unreadCount || pendingId !== null} onClick={() => void markAllRead()}><CheckCheck size={17} />全部标为已读</button>
    </header>
    <section className="filter-row">
      <select aria-label="阅读或投递状态" value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="">全部状态</option><option value="UNREAD">未读</option><option value="READ">已读</option><option value="FAILED">投递失败</option><option value="SENT">已投递</option><option value="PENDING">待投递</option>
      </select>
      <select aria-label="业务类型" value={businessType} onChange={(event) => setBusinessType(event.target.value)}>
        <option value="">全部业务</option><option value="REPAIR">报修</option><option value="ANNOUNCEMENT">公告</option><option value="BILLING">账单</option><option value="INSPECTION">巡检安防</option>
      </select>
    </section>
    {operationError && <section className="content-panel"><ErrorState error={operationError} /></section>}
    <section className="content-panel">
      {messages.loading ? <Loading /> : messages.error ? <ErrorState error={messages.error} retry={() => void messages.reload()} /> : !messages.data?.items.length ? <Empty title="没有匹配消息" /> : <div className="entity-list">{messages.data.items.map((item) => <article className={`entity-card ${item.is_read ? "" : "unread"}`} key={item.id}>
        <span className="entity-icon"><Bell /></span>
        <div className="entity-main">
          <div><span className={`status ${item.status.toLowerCase()}`}>{item.status} · {item.is_read ? "已读" : "未读"}</span>{!item.is_read && <button className="text-button" disabled={pendingId !== null} onClick={() => void markRead(item.id)}><Check size={15} />标为已读</button>}</div>
          <h3>{item.title}</h3><p>{item.body}</p><small>{item.business_type} · {new Date(item.created_at).toLocaleString("zh-CN")}</small>
          {item.status === "FAILED" && <div className="failure-note">已重试 {item.retry_count}/{item.max_retry_count} 次 · 人工接管：{item.handover_status ?? "NOT_CREATED"} · 备用联系：{item.fallback_contact ?? "未配置"}</div>}
        </div>
      </article>)}</div>}
    </section>
  </>;
}
