import { FileText, Plus } from "lucide-react";
import { apiRequest } from "../api/client";
import type { Announcement, ListResult } from "../api/contracts";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function AnnouncementsPage() {
  const { data, error, loading, reload } = useApi(() => apiRequest<ListResult<Announcement>>("/api/announcements"));
  return <><header className="page-heading"><div><span className="eyebrow">ANNOUNCEMENTS</span><h1>社区公告</h1><p>查看已发布通知；有权限的人员可进入草稿和审核流程。</p></div><button className="button primary" disabled title="公告后端尚未装配"><Plus size={17} />新建草稿</button></header><section className="content-panel"><div className="panel-heading"><h2>公告列表</h2><span className="safe-note">发布与撤回只能由授权人员完成</span></div>{loading ? <Loading /> : error ? <ErrorState error={error} retry={() => void reload()} /> : !data?.items.length ? <Empty title="暂无可见公告" /> : <div className="entity-list">{data.items.map((item) => <article className="entity-card" key={item.id}><span className="entity-icon"><FileText /></span><div className="entity-main"><span className={`status ${item.status.toLowerCase()}`}>{item.status}</span><h3>{item.title}</h3><p>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "尚未发布"}</p></div></article>)}</div>}</section></>;
}
