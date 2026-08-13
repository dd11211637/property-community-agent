import { Clock3, Plus, Wrench, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { ListResult, StaffOption, TimelineEntry, WorkOrder } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog, type ActionField } from "../components/ActionDialog";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

const actionLabels: Record<string, string> = { ASSIGN: "派单", ACCEPT: "接单", REJECT: "拒单", RECORD_PROGRESS: "记录进度", SUBMIT_COMPLETION: "提交完工", SUBMIT_REWORK_COMPLETION: "提交返工完工", VERIFY_PASS: "验收通过", REQUEST_REWORK: "要求返工", CREATE_REVIEW: "评价" };
const actionFields: Record<string, ActionField[]> = {
  REJECT: [{ name: "reason", label: "拒单原因", type: "textarea", required: true }],
  RECORD_PROGRESS: [{ name: "record_type", label: "记录类型", type: "select", options: ["APPOINTMENT", "ARRIVAL", "PROGRESS", "BLOCKED"].map((value) => ({ value, label: value })) }, { name: "note", label: "进度说明", type: "textarea", required: true }],
  SUBMIT_COMPLETION: [{ name: "note", label: "完工说明", type: "textarea", required: true }],
  SUBMIT_REWORK_COMPLETION: [{ name: "note", label: "返工完工说明", type: "textarea", required: true }],
  REQUEST_REWORK: [{ name: "reason", label: "返工原因", type: "textarea", required: true }],
  CREATE_REVIEW: [{ name: "rating", label: "评分（1-5）", type: "number", initial: "5", required: true }, { name: "comment", label: "评价内容", type: "textarea" }],
};

function RepairDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const detail = useApi(() => apiRequest<WorkOrder>(`/api/work-orders/${id}`), id);
  const timeline = useApi(() => apiRequest<TimelineEntry[]>(`/api/work-orders/${id}/timeline`), id);
  const [action, setAction] = useState<string | null>(null);
  const [staffOptions, setStaffOptions] = useState<Array<{ value: string; label: string }>>([]);
  async function openAction(nextAction: string) {
    if (nextAction === "ASSIGN") {
      const staff = await apiRequest<StaffOption[]>("/api/staff?role=REPAIR_WORKER");
      setStaffOptions(staff.map((item) => ({ value: item.id, label: item.display_name })));
    }
    setAction(nextAction);
  }
  async function execute(values: Record<string, string>) {
    if (!detail.data || !action) return;
    const common = { expected_version: detail.data.version };
    if (action === "CREATE_REVIEW") {
      await apiRequest(`/api/work-orders/${id}/reviews`, { method: "POST", idempotencyKey: createIdempotencyKey("repair-review"), body: { rating: Number(values.rating), comment: values.comment || null } });
    } else {
      const endpointAction = { RECORD_PROGRESS: "record-progress", SUBMIT_COMPLETION: "submit-completion", SUBMIT_REWORK_COMPLETION: "submit-completion", VERIFY_PASS: "verify-pass", REQUEST_REWORK: "request-rework" }[action] ?? action.toLowerCase();
      const extra = action === "ASSIGN" ? { assignee_id: values.assignee_id } : action === "REJECT" || action === "REQUEST_REWORK" ? { reason: values.reason } : action === "RECORD_PROGRESS" ? { record_type: values.record_type, note: values.note, attachment_ids: [] } : action.includes("COMPLETION") ? { note: values.note, attachment_ids: [] } : {};
      await apiRequest(`/api/work-orders/${id}/actions/${endpointAction}`, { method: "POST", idempotencyKey: createIdempotencyKey(`repair-${action.toLowerCase()}`), body: { ...common, ...extra } });
    }
    await Promise.all([detail.reload(), timeline.reload(), onChanged()]);
  }
  const fields: ActionField[] = action === "ASSIGN" ? [{ name: "assignee_id", label: "维修人员", type: "select", required: true, options: staffOptions }] : action ? actionFields[action] : [];
  return <section className="detail-panel"><div className="panel-heading"><h2>工单详情</h2><button className="icon-button" aria-label="关闭详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : detail.data && <><div className="detail-grid"><div><small>业务编号</small><b>{detail.data.business_no ?? detail.data.id}</b></div><div><small>状态 / 版本</small><b>{detail.data.status} / v{detail.data.version}</b></div><div><small>位置</small><b>{detail.data.location}</b></div><div><small>处理人</small><b>{detail.data.assignee_id ?? "未分派"}</b></div></div><p className="detail-description">{detail.data.description}</p><div className="action-row">{detail.data.available_actions?.map((item) => <button className="button ghost" key={item} onClick={() => void openAction(item)}>{actionLabels[item] ?? item}</button>)}</div><h3 className="section-title">处理时间线</h3>{timeline.loading ? <Loading /> : timeline.error ? <ErrorState error={timeline.error} retry={() => void timeline.reload()} /> : !timeline.data?.length ? <Empty title="暂无时间线记录" /> : <div className="timeline">{timeline.data.map((item, index) => <div key={`${item.created_at}-${index}`}><span /><div><b>{item.action}</b><p>{item.from_status ?? "创建"} → {item.to_status ?? detail.data?.status}</p>{(item.reason || item.note) && <small>{item.reason ?? item.note}</small>}<time>{new Date(item.created_at).toLocaleString("zh-CN")}</time></div></div>)}</div>}</>}{action && <ActionDialog title={actionLabels[action] ?? action} fields={fields} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
}

export function RepairsPage() {
  const { session } = useAuth();
  const list = useApi(() => apiRequest<ListResult<WorkOrder>>("/api/work-orders"));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState({ category: "OTHER", location: "", description: "", urgency: "NORMAL" });
  const [confirm, setConfirm] = useState(false);
  const [operationKey, setOperationKey] = useState("");
  const stage = (event: FormEvent) => { event.preventDefault(); setOperationKey(createIdempotencyKey("repair-create")); setConfirm(true); };
  const create = async () => {
    if (!session?.current_house_id) throw new Error("请先选择当前房屋。");
    const parameters = { ...draft, house_id: session.current_house_id, attachment_ids: [] };
    const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "CREATE_WORK_ORDER", parameters } });
    await apiRequest("/api/work-orders", { method: "POST", idempotencyKey: operationKey, body: { ...parameters, confirmation_token: confirmation.token } });
    setFormOpen(false); setDraft({ category: "OTHER", location: "", description: "", urgency: "NORMAL" }); await list.reload();
  };
  return <><header className="page-heading"><div><span className="eyebrow">REPAIR SERVICE</span><h1>报修服务</h1><p>提交问题，查看处理时间线与下一步操作。</p></div><button className="button primary" onClick={() => setFormOpen(true)}><Plus size={17} />新建报修</button></header>{formOpen && <form className="form-card" onSubmit={stage}><div className="form-card-title"><Wrench /><div><h2>描述需要处理的问题</h2><p>只填写必要信息，提交前会再次核对。</p></div></div><div className="form-grid"><label>问题类型<select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}><option value="WATER_PLUMBING">水暖管道</option><option value="ELECTRICAL">电气</option><option value="ELEVATOR">电梯</option><option value="OTHER">其他</option></select></label><label>紧急程度<select value={draft.urgency} onChange={(e) => setDraft({ ...draft, urgency: e.target.value })}><option value="NORMAL">普通</option><option value="URGENT">紧急</option></select></label><label className="span-2">具体位置<input required maxLength={128} value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} /></label><label className="span-2">问题描述<textarea required value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label></div><div className="form-actions"><button type="button" className="button ghost" onClick={() => setFormOpen(false)}>取消</button><button className="button primary">核对并提交</button></div></form>}<div className={selectedId ? "master-detail" : ""}><section className="content-panel"><div className="panel-heading"><h2>工单列表</h2><button className="text-button" onClick={() => void list.reload()}>刷新</button></div>{list.loading ? <Loading /> : list.error ? <ErrorState error={list.error} retry={() => void list.reload()} /> : !list.data?.items.length ? <Empty title="还没有报修记录" /> : <div className="entity-list">{list.data.items.map((item) => <button className="entity-card entity-button" key={item.id} onClick={() => setSelectedId(item.id)}><div className="entity-icon"><Wrench /></div><div className="entity-main"><div><span className={`status ${item.status.toLowerCase()}`}>{item.status}</span><small>#{item.business_no ?? item.id.slice(0, 8)}</small></div><h3>{item.location}</h3><p>{item.description}</p><span className="meta"><Clock3 size={14} />版本 {item.version} · {item.urgency}</span></div></button>)}</div>}</section>{selectedId && <RepairDetail id={selectedId} onClose={() => setSelectedId(null)} onChanged={list.reload} />}</div>{confirm && <ConfirmDialog title="确认创建报修工单" summary={<dl className="summary-list"><div><dt>房屋</dt><dd>{session?.houses.find((h) => h.id === session.current_house_id)?.label ?? "未选择"}</dd></div><div><dt>位置</dt><dd>{draft.location}</dd></div><div><dt>描述</dt><dd>{draft.description}</dd></div></dl>} onClose={() => setConfirm(false)} onConfirm={create} />}</>;
}
