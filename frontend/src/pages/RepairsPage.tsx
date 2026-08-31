import { CalendarClock, Clock3, MapPin, Phone, Plus, UserRound, Wrench, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { ListResult, StaffOption, TimelineEntry, WorkOrder } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog, type ActionField } from "../components/ActionDialog";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";
import { StatusBadge } from "../components/StatusBadge";
import { businessReference, displayDate, displayLabel, localDateTimeToIso } from "../ui/display";
import { workspaceFor } from "../ui/roles";

const actionLabels: Record<string, string> = { ASSIGN: "派单", ACCEPT: "接单", REJECT: "拒单", RECORD_PROGRESS: "记录进度", SUBMIT_COMPLETION: "提交完工", SUBMIT_REWORK_COMPLETION: "提交返工完工", VERIFY_PASS: "验收通过", REQUEST_REWORK: "要求返工", CREATE_REVIEW: "评价" };
const actionFields: Record<string, ActionField[]> = {
  REJECT: [{ name: "reason", label: "拒单原因", type: "textarea", required: true }],
  RECORD_PROGRESS: [{ name: "record_type", label: "记录类型", type: "select", options: [{ value: "APPOINTMENT", label: "预约上门" }, { value: "ARRIVAL", label: "已到现场" }, { value: "PROGRESS", label: "处理进展" }, { value: "BLOCKED", label: "暂时受阻" }] }, { name: "appointment_at", label: "上门时间（预约上门时必填）", type: "datetime-local" }, { name: "note", label: "进度说明", type: "textarea", required: true }],
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
      if (action === "RECORD_PROGRESS" && values.record_type === "APPOINTMENT" && !values.appointment_at) throw new Error("请选择预约上门时间。");
      const extra = action === "ASSIGN" ? { assignee_id: values.assignee_id } : action === "REJECT" || action === "REQUEST_REWORK" ? { reason: values.reason } : action === "RECORD_PROGRESS" ? { record_type: values.record_type, appointment_at: values.appointment_at ? localDateTimeToIso(values.appointment_at) : null, note: values.note, attachment_ids: [] } : action.includes("COMPLETION") ? { note: values.note, attachment_ids: [] } : {};
      await apiRequest(`/api/work-orders/${id}/actions/${endpointAction}`, { method: "POST", idempotencyKey: createIdempotencyKey(`repair-${action.toLowerCase()}`), body: { ...common, ...extra } });
    }
    await Promise.all([detail.reload(), timeline.reload(), onChanged()]);
  }
  const fields: ActionField[] = action === "ASSIGN" ? [{ name: "assignee_id", label: "维修人员", type: "select", required: true, options: staffOptions }] : action ? actionFields[action] : [];
  const dialablePhone = detail.data?.resident_phone?.match(/^\+?\d{7,15}$/)?.[0];
  return <section className="detail-panel"><div className="panel-heading"><div><span className="safe-note">{detail.data ? businessReference(detail.data.business_no, "当前工单") : "工单"}</span><h2>工单详情</h2></div><button className="icon-button" aria-label="关闭详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : detail.data && <><div className="repair-detail-summary"><div className="repair-summary-heading"><div><span>报修位置</span><strong>{detail.data.location}</strong></div><StatusBadge value={detail.data.status} /></div><p>{detail.data.description}</p></div><section className="visit-card" aria-labelledby="visit-heading"><div className="visit-card-heading"><div><span className="eyebrow">上门信息</span><h3 id="visit-heading">联系住户</h3></div>{dialablePhone && <a className="button primary contact-button" href={`tel:${dialablePhone}`}><Phone size={16} aria-hidden="true" />拨打电话</a>}</div><dl className="visit-details"><div><dt><UserRound aria-hidden="true" />住户</dt><dd>{detail.data.resident_name ?? "暂无联系人信息"}</dd></div><div><dt><Phone aria-hidden="true" />联系电话</dt><dd>{detail.data.resident_phone ?? "暂未登记"}</dd></div><div><dt><MapPin aria-hidden="true" />具体地址</dt><dd>{detail.data.house_address ? `${detail.data.house_address} · ${detail.data.location}` : "暂无房屋地址"}</dd></div><div><dt><CalendarClock aria-hidden="true" />预约上门</dt><dd>{detail.data.appointment_at ? displayDate(detail.data.appointment_at) : "尚未预约，请先联系住户确认"}</dd></div></dl></section><div className="action-row primary-actions">{detail.data.available_actions?.map((item) => <button className="button ghost" key={item} onClick={() => void openAction(item)}>{actionLabels[item] ?? "继续处理"}</button>)}</div><h3 className="section-title">处理进度</h3>{timeline.loading ? <Loading /> : timeline.error ? <ErrorState error={timeline.error} retry={() => void timeline.reload()} /> : !timeline.data?.length ? <Empty title="暂无进度记录" detail="工单开始流转后，关键进度会显示在这里。" /> : <div className="timeline">{timeline.data.map((item, index) => <div key={`${item.created_at}-${index}`}><span /><div><b>{actionLabels[item.action] ?? "工单进展"}</b><p>{displayLabel(item.from_status, "已创建")} → {displayLabel(item.to_status ?? detail.data?.status)}</p>{item.appointment_at && <small>上门时间：{displayDate(item.appointment_at)}</small>}{(item.reason || item.note) && <small>{item.reason ?? item.note}</small>}<time>{displayDate(item.created_at)}</time></div></div>)}</div>}</>}{action && <ActionDialog title={actionLabels[action] ?? "继续处理"} fields={fields} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
}

export function RepairsPage() {
  const { session } = useAuth();
  const workspace = workspaceFor(session?.actor.roles);
  const list = useApi(() => apiRequest<ListResult<WorkOrder>>("/api/work-orders"));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState({ category: "OTHER", location: "", description: "", urgency: "NORMAL", appointment_at: "" });
  const [confirm, setConfirm] = useState(false);
  const [operationKey, setOperationKey] = useState("");
  const stage = (event: FormEvent) => { event.preventDefault(); setOperationKey(createIdempotencyKey("repair-create")); setConfirm(true); };
  const create = async () => {
    if (!session?.current_house_id) throw new Error("请先选择当前房屋。");
    const parameters = { ...draft, appointment_at: draft.appointment_at ? localDateTimeToIso(draft.appointment_at) : null, house_id: session.current_house_id, attachment_ids: [] };
    const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "CREATE_WORK_ORDER", parameters } });
    await apiRequest("/api/work-orders", { method: "POST", idempotencyKey: operationKey, body: { ...parameters, confirmation_token: confirmation.token } });
    setFormOpen(false); setDraft({ category: "OTHER", location: "", description: "", urgency: "NORMAL", appointment_at: "" }); await list.reload();
  };
  const heading = workspace === "resident" ? ["我的报修", "提交问题，随时查看服务进度。"] : workspace === "admin" ? ["工单调度", "查看待分配、处理中和待验收事项。"] : ["维修任务", "按优先级处理分配给你的工单。"];
  return <><header className="page-heading"><div><span className="eyebrow">{workspace === "resident" ? "社区维修服务" : "任务执行"}</span><h1>{heading[0]}</h1><p>{heading[1]}</p></div>{workspace === "resident" && <button className="button primary" onClick={() => setFormOpen(true)}><Plus size={17} />新建报修</button>}</header>{formOpen && <form className="form-card" onSubmit={stage}><div className="form-card-title"><Wrench /><div><h2>描述需要处理的问题</h2><p>只填写必要信息，提交前会再次核对。</p></div></div><div className="form-grid"><label>问题类型<select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}><option value="WATER_PLUMBING">水暖管道</option><option value="ELECTRICAL">电气</option><option value="ELEVATOR">电梯</option><option value="OTHER">其他</option></select></label><label>紧急程度<select value={draft.urgency} onChange={(e) => setDraft({ ...draft, urgency: e.target.value })}><option value="NORMAL">普通</option><option value="URGENT">紧急</option></select></label><label className="span-2">具体位置<input required maxLength={128} value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} /></label><label className="span-2">问题描述<textarea required value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label><label className="span-2">预约上门时间（可选）<input type="datetime-local" value={draft.appointment_at} onChange={(e) => setDraft({ ...draft, appointment_at: e.target.value })} /></label></div><div className="form-actions"><button type="button" className="button ghost" onClick={() => setFormOpen(false)}>取消</button><button className="button primary">核对并提交</button></div></form>}<div className={selectedId ? "master-detail" : ""}><section className="content-panel"><div className="panel-heading"><h2>{workspace === "resident" ? "报修进度" : "任务队列"}</h2><button className="text-button" onClick={() => void list.reload()}>刷新</button></div>{list.loading ? <Loading /> : list.error ? <ErrorState error={list.error} retry={() => void list.reload()} /> : !list.data?.items.length ? <Empty title={workspace === "resident" ? "还没有报修记录" : "当前没有待处理任务"} /> : <div className="repair-list">{list.data.items.map((item) => <button className="repair-item entity-button" data-testid="repair-item" key={item.id} onClick={() => setSelectedId(item.id)}><div className="repair-item-main"><div><StatusBadge value={item.status} /><small>{businessReference(item.business_no, "社区维修工单")}</small></div><h3>{item.location}</h3><p>{item.description}</p></div><span className="repair-next"><Clock3 aria-hidden="true" />{displayLabel(item.urgency, "普通")}</span></button>)}</div>}</section>{selectedId && <RepairDetail id={selectedId} onClose={() => setSelectedId(null)} onChanged={list.reload} />}</div>{confirm && <ConfirmDialog title="确认创建报修工单" summary={<dl className="summary-list"><div><dt>房屋</dt><dd>{session?.houses.find((h) => h.id === session.current_house_id)?.label ?? "未选择"}</dd></div><div><dt>位置</dt><dd>{draft.location}</dd></div><div><dt>描述</dt><dd>{draft.description}</dd></div><div><dt>预约上门时间</dt><dd>{draft.appointment_at ? displayDate(draft.appointment_at) : "稍后协商"}</dd></div></dl>} onClose={() => setConfirm(false)} onConfirm={create} />}</>;
}
