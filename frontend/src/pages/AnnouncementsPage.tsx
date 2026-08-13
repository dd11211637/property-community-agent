import { FileText, Plus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { Announcement, AnnouncementVersion, AudiencePreview, ListResult } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog, type ActionField } from "../components/ActionDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

const labels: Record<string, string> = { EDIT: "编辑草稿", SUBMIT_REVIEW: "送审", APPROVE: "批准", REJECT: "驳回", PUBLISH: "确认发布", SCHEDULE: "定时发布", WITHDRAW: "撤回" };

function audience(values: Record<string, string>) {
  const buildings = values.building_ids?.split(",").map((value) => value.trim()).filter(Boolean)
    .map((value) => /^\d+$/.test(value) ? `${value}栋` : value) ?? [];
  return buildings.length ? { building_ids: buildings } : {};
}

function AnnouncementDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const { session } = useAuth();
  const detail = useApi(() => apiRequest<Announcement>(`/api/announcements/${id}`), id);
  const [preview, setPreview] = useState<AudiencePreview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [versions, setVersions] = useState<AnnouncementVersion[] | null>(null);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState<unknown>(null);
  const [action, setAction] = useState<string | null>(null);
  const item = detail.data;
  const canViewVersions = session?.actor.roles.some((role) => role === "CUSTOMER_SERVICE" || role === "MANAGER") ?? false;
  const fields: ActionField[] = action === "EDIT" && item ? [{ name: "title", label: "标题", required: true, initial: item.title }, { name: "body", label: "正文", type: "textarea", required: true, initial: item.body }, { name: "category", label: "分类", required: true, initial: item.category }, { name: "building_ids", label: "楼栋编号（逗号分隔，留空代表全社区）", initial: item.audience_condition.building_ids?.join(",") ?? "" }] : action === "SCHEDULE" ? [{ name: "scheduled_at", label: "发布时间", type: "datetime-local", required: true }] : action === "REJECT" || action === "WITHDRAW" ? [{ name: "reason", label: action === "REJECT" ? "驳回原因" : "撤回原因", type: "textarea", required: true }] : [];
  async function loadPreview() {
    setPreviewError(null);
    try { setPreview(await apiRequest<AudiencePreview>(`/api/announcements/${id}/audience-preview`)); } catch (error) { setPreviewError(error); }
  }
  async function loadVersions() {
    if (versions !== null) {
      setVersions(null);
      return;
    }
    setVersionsLoading(true);
    setVersionsError(null);
    try {
      setVersions(await apiRequest<AnnouncementVersion[]>(`/api/announcements/${id}/versions`));
    } catch (error) {
      setVersionsError(error);
    } finally {
      setVersionsLoading(false);
    }
  }
  async function execute(values: Record<string, string>) {
    if (!item || !action) return;
    const idempotencyKey = createIdempotencyKey(`announcement-${action.toLowerCase()}`);
    if (action === "EDIT") {
      await apiRequest(`/api/announcements/${id}`, { method: "PATCH", idempotencyKey, body: { title: values.title, body: values.body, category: values.category, audience_condition: audience(values), scheduled_at: null, expected_version: item.version } });
    } else if (action === "PUBLISH") {
      const parameters = { announcement_id: id, expected_version: item.version, action: "PUBLISH" };
      const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "ANNOUNCEMENT_PUBLISH", parameters } });
      await apiRequest(`/api/announcements/${id}/actions/publish`, { method: "POST", idempotencyKey, body: { expected_version: item.version, confirmation_token: confirmation.token } });
    } else if (action === "SCHEDULE") {
      const scheduledAt = new Date(values.scheduled_at).toISOString().replace("Z", "+00:00");
      const parameters = { announcement_id: id, expected_version: item.version, scheduled_at: scheduledAt };
      const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "ANNOUNCEMENT_SCHEDULE", parameters } });
      await apiRequest(`/api/announcements/${id}/actions/schedule`, { method: "POST", idempotencyKey, body: { expected_version: item.version, scheduled_at: scheduledAt, confirmation_token: confirmation.token } });
    } else {
      const path = action === "SUBMIT_REVIEW" ? "submit-review" : `actions/${action.toLowerCase()}`;
      await apiRequest(`/api/announcements/${id}/${path}`, { method: "POST", idempotencyKey, body: { expected_version: item.version, ...(values.reason ? { reason: values.reason } : {}) } });
    }
    setPreview(null);
    if (versions !== null) {
      try {
        setVersions(await apiRequest<AnnouncementVersion[]>(`/api/announcements/${id}/versions`));
        setVersionsError(null);
      } catch (error) {
        setVersionsError(error);
      }
    }
    await Promise.all([detail.reload(), onChanged()]);
  }
  return <section className="detail-panel">
    <div className="panel-heading">
      <h2>公告详情</h2>
      <button className="icon-button" aria-label="关闭详情" onClick={onClose}><X /></button>
    </div>
    {detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : item && <>
      <div className="detail-grid">
        <div><small>业务编号</small><b>{item.business_no ?? item.id}</b></div>
        <div><small>状态 / 版本</small><b>{item.status} / v{item.version}</b></div>
        <div><small>分类</small><b>{item.category}</b></div>
        <div><small>受众条件</small><b>{Object.keys(item.audience_condition).length ? JSON.stringify(item.audience_condition) : "全社区"}</b></div>
        {item.scheduled_at && <div><small>计划发布时间</small><b>{new Date(item.scheduled_at).toLocaleString("zh-CN")}</b></div>}
      </div>
      <h3 className="section-title">{item.title}</h3>
      <p className="detail-description preserve-lines">{item.body}</p>
      {item.manager_recheck_required && <p className="warning-note">此公告需要管理者再次核查。</p>}
      <div className="action-row">
        {item.available_actions?.map((value) => <button className="button ghost" key={value} onClick={() => setAction(value)}>{labels[value] ?? value}</button>)}
        {item.available_actions?.some((value) => ["EDIT", "SUBMIT_REVIEW", "PUBLISH"].includes(value)) && <button className="button ghost" onClick={() => void loadPreview()}>预览受众</button>}
        {canViewVersions && <button className="button ghost" onClick={() => void loadVersions()}>{versions === null ? "查看版本历史" : "收起版本历史"}</button>}
      </div>
      {previewError ? <ErrorState error={previewError} retry={() => void loadPreview()} /> : null}
      {preview && <div className="audience-preview"><b>预计触达 {preview.count} 人</b><p>{preview.samples.map((sample) => `${sample.receiver ?? "用户"} · ${sample.address ?? ""}`).join("；") || "暂无样例"}</p></div>}
      {versionsLoading && <Loading />}
      {versionsError ? <ErrorState error={versionsError} retry={() => void loadVersions()} /> : null}
      {versions && <section className="version-history" aria-label="版本历史">
        <h3 className="section-title">版本历史</h3>
        {!versions.length ? <Empty title="暂无版本记录" /> : versions.map((version) => <article className="version-card" key={version.version_no}>
          <div><b>v{version.version_no} · {version.title}</b><span>{new Date(version.created_at).toLocaleString("zh-CN")}</span></div>
          <small>{version.category} · {version.source === "MANUAL" ? "人工编辑" : "采纳智能建议"}</small>
          <p className="preserve-lines">{version.body}</p>
        </article>)}
      </section>}
    </>}
    {action && <ActionDialog title={labels[action] ?? action} fields={fields} confirmLabel={action === "PUBLISH" ? "二次确认并发布" : action === "SCHEDULE" ? "确认定时发布" : undefined} onClose={() => setAction(null)} onConfirm={execute} />}
  </section>;
}

export function AnnouncementsPage() {
  const { session } = useAuth();
  const canCreate = session?.actor.roles.some((role) => role === "CUSTOMER_SERVICE" || role === "MANAGER") ?? false;
  const list = useApi(() => apiRequest<ListResult<Announcement>>("/api/announcements"));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "", category: "GENERAL", building_ids: "" });
  const [formError, setFormError] = useState<unknown>(null);
  async function create(event: FormEvent) {
    event.preventDefault(); setFormError(null);
    try { const created = await apiRequest<Announcement>("/api/announcements", { method: "POST", idempotencyKey: createIdempotencyKey("announcement-create"), body: { title: draft.title, body: draft.body, category: draft.category, audience_condition: audience(draft), scheduled_at: null } }); setFormOpen(false); setDraft({ title: "", body: "", category: "GENERAL", building_ids: "" }); setSelectedId(created.id); await list.reload(); } catch (error) { setFormError(error); }
  }
  return <><header className="page-heading"><div><span className="eyebrow">ANNOUNCEMENTS</span><h1>社区公告</h1><p>住户仅查看命中受众的已发布公告；工作人员按权限完成审核发布。</p></div>{canCreate && <button className="button primary" onClick={() => setFormOpen(true)}><Plus size={17} />新建草稿</button>}</header>{formOpen && <form className="form-card" onSubmit={(event) => void create(event)}><div className="form-card-title"><FileText /><div><h2>新建公告草稿</h2><p>受众为空时代表全社区，发布前可预览。</p></div></div><div className="form-grid"><label className="span-2">标题<input required maxLength={128} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label><label>分类<select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}><option value="GENERAL">一般通知</option><option value="MAINTENANCE">维护通知</option><option value="SAFETY">安全通知</option><option value="EMERGENCY">紧急通知</option></select></label><label>楼栋编号<input placeholder="例如 1,2" value={draft.building_ids} onChange={(event) => setDraft({ ...draft, building_ids: event.target.value })} /></label><label className="span-2">正文<textarea required value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} /></label></div>{formError ? <ErrorState error={formError} /> : null}<div className="form-actions"><button type="button" className="button ghost" onClick={() => setFormOpen(false)}>取消</button><button className="button primary">保存草稿</button></div></form>}<div className={selectedId ? "master-detail" : ""}><section className="content-panel"><div className="panel-heading"><h2>公告列表</h2><span className="safe-note">发布与撤回只能由授权人员完成</span></div>{list.loading ? <Loading /> : list.error ? <ErrorState error={list.error} retry={() => void list.reload()} /> : !list.data?.items.length ? <Empty title="暂无可见公告" /> : <div className="entity-list">{list.data.items.map((item) => <button className="entity-card entity-button" key={item.id} onClick={() => setSelectedId(item.id)}><span className="entity-icon"><FileText /></span><div className="entity-main"><span className={`status ${item.status.toLowerCase()}`}>{item.status}</span><h3>{item.title}</h3><p>{item.body}</p><small>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "尚未发布"}</small></div></button>)}</div>}</section>{selectedId && <AnnouncementDetail id={selectedId} onClose={() => setSelectedId(null)} onChanged={list.reload} />}</div></>;
}
