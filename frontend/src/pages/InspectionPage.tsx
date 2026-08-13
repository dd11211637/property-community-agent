import { ClipboardCheck, Plus, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { InspectionTask, ListResult, SecurityEvent, StaffOption, TimelineEntry } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog, type ActionField } from "../components/ActionDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

const taskLabels: Record<string, string> = { ASSIGN: "分派", START: "开始巡检", SUBMIT_RECORDS: "提交记录", ADD_RECORD: "追加记录", COMPLETE: "复核完成" };
const eventLabels: Record<string, string> = { ASSIGN: "分派处置", SUBMIT_DISPOSAL: "提交处置", GRADE_CONFIRM: "确认高风险评级", REVIEW_PASS: "复核通过", RETURN: "退回处置" };
const recordOptions = ["CHECKIN", "POINT_RECORD", "PROGRESS", "COMPLETION"].map((value) => ({ value, label: value }));

function Timeline({ loader, dependencyKey }: { loader: () => Promise<TimelineEntry[]>; dependencyKey: string }) {
  const timeline = useApi(loader, dependencyKey);
  return <><h3 className="section-title">状态时间线</h3>{timeline.loading ? <Loading /> : timeline.error ? <ErrorState error={timeline.error} retry={() => void timeline.reload()} /> : !timeline.data?.length ? <Empty title="暂无时间线记录" /> : <div className="timeline">{timeline.data.map((item, index) => <div key={`${item.created_at}-${index}`}><span /><div><b>{item.action}</b><p>{item.from_status ?? "创建"} → {item.to_status ?? "记录"}</p>{(item.reason || item.note) && <small>{item.reason ?? item.note}</small>}<time>{new Date(item.created_at).toLocaleString("zh-CN")}</time></div></div>)}</div>}</>;
}

function TaskDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const detail = useApi(() => apiRequest<InspectionTask>(`/api/inspection-tasks/${id}`), id);
  const [action, setAction] = useState<string | null>(null);
  const [staffOptions, setStaffOptions] = useState<Array<{ value: string; label: string }>>([]);
  async function openAction(nextAction: string) { if (nextAction === "ASSIGN") { const staff = await apiRequest<StaffOption[]>("/api/staff?role=SECURITY_GUARD"); setStaffOptions(staff.map((item) => ({ value: item.id, label: item.display_name }))); } setAction(nextAction); }
  const fields: ActionField[] = action === "ASSIGN" ? [{ name: "assignee_id", label: "安保人员", type: "select", required: true, options: staffOptions }] : action === "SUBMIT_RECORDS" || action === "ADD_RECORD" ? [{ name: "record_type", label: "记录类型", type: "select", options: recordOptions }, { name: "point", label: "点位" }, { name: "note", label: "记录内容", type: "textarea", required: true }] : [];
  async function execute(values: Record<string, string>) {
    if (!detail.data || !action) return;
    let extra: Record<string, unknown> = {};
    if (action === "ASSIGN") extra = { assignee_id: values.assignee_id };
    if (action === "ADD_RECORD") extra = { record_type: values.record_type, point: values.point || null, note: values.note, attachment_ids: [], is_supplement: false };
    if (action === "SUBMIT_RECORDS") {
      const parameters = { note: values.note, record_type: values.record_type, point: values.point || null };
      const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "INSPECTION_TASK_SUBMIT_RECORDS", parameters } });
      extra = { ...parameters, confirmation_token: confirmation.token, attachment_ids: [] };
    }
    const endpoint = { SUBMIT_RECORDS: "submit-records", ADD_RECORD: "add-record" }[action as "SUBMIT_RECORDS" | "ADD_RECORD"] ?? action.toLowerCase();
    await apiRequest(`/api/inspection-tasks/${id}/actions/${endpoint}`, { method: "POST", idempotencyKey: createIdempotencyKey(`inspection-${action.toLowerCase()}`), body: { expected_version: detail.data.version, ...extra } });
    await Promise.all([detail.reload(), onChanged()]);
  }
  const task = detail.data;
  return <section className="detail-panel"><div className="panel-heading"><h2>巡检任务详情</h2><button className="icon-button" aria-label="关闭任务详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : task && <><div className="detail-grid"><div><small>业务编号</small><b>{task.business_no ?? task.id}</b></div><div><small>状态 / 版本</small><b>{task.status} / v{task.version}</b></div><div><small>执行人</small><b>{task.assignee_id ?? "未分派"}</b></div><div><small>路线点位</small><b>{task.route_points?.join("、") ?? "无"}</b></div></div><p className="detail-description">{task.description}</p><div className="action-row">{task.available_actions?.map((value) => <button className="button ghost" key={value} onClick={() => void openAction(value)}>{taskLabels[value] ?? value}</button>)}</div><Timeline loader={() => apiRequest<TimelineEntry[]>(`/api/inspection-tasks/${id}/timeline`)} dependencyKey={`${id}-${task.version}`} /></>}{action && <ActionDialog title={taskLabels[action] ?? action} fields={fields} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
}

function EventDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const detail = useApi(() => apiRequest<SecurityEvent>(`/api/security-events/${id}`), id);
  const [action, setAction] = useState<string | null>(null);
  const [staffOptions, setStaffOptions] = useState<Array<{ value: string; label: string }>>([]);
  async function openAction(nextAction: string) { if (nextAction === "ASSIGN") { const staff = await apiRequest<StaffOption[]>("/api/staff?role=SECURITY_GUARD"); setStaffOptions(staff.map((item) => ({ value: item.id, label: item.display_name }))); } setAction(nextAction); }
  const fields: ActionField[] = action === "ASSIGN" ? [{ name: "assignee_id", label: "处置人员", type: "select", required: true, options: staffOptions }] : action === "SUBMIT_DISPOSAL" ? [{ name: "note", label: "处置记录", type: "textarea", required: true }] : action === "RETURN" ? [{ name: "note", label: "退回原因", type: "textarea", required: true }] : [];
  async function execute(values: Record<string, string>) {
    if (!detail.data || !action) return;
    const endpoint = { SUBMIT_DISPOSAL: "submit-disposal", GRADE_CONFIRM: "grade-confirm", REVIEW_PASS: "review-pass" }[action as "SUBMIT_DISPOSAL" | "GRADE_CONFIRM" | "REVIEW_PASS"] ?? action.toLowerCase();
    const extra = action === "ASSIGN" ? { assignee_id: values.assignee_id } : action === "SUBMIT_DISPOSAL" ? { note: values.note, attachment_ids: [] } : action === "RETURN" ? { note: values.note } : {};
    await apiRequest(`/api/security-events/${id}/actions/${endpoint}`, { method: "POST", idempotencyKey: createIdempotencyKey(`security-${action.toLowerCase()}`), body: { expected_version: detail.data.version, ...extra } });
    await Promise.all([detail.reload(), onChanged()]);
  }
  const event = detail.data;
  return <section className="detail-panel"><div className="panel-heading"><h2>安防事件详情</h2><button className="icon-button" aria-label="关闭事件详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : event && <><div className="detail-grid"><div><small>业务编号</small><b>{event.business_no ?? event.id}</b></div><div><small>状态 / 版本</small><b>{event.status} / v{event.version}</b></div><div><small>风险等级</small><b>{event.risk_level}</b></div><div><small>处理人</small><b>{event.assignee_id ?? "未分派"}</b></div></div><p className="detail-description">{event.location}：{event.description}</p>{event.risk_level === "HIGH_RISK" && !event.grade_confirmed_by && <div className="warning-note">高风险事件必须先由授权管理者确认评级，不能直接关闭。</div>}<div className="action-row">{event.available_actions?.map((value) => <button className="button ghost" key={value} onClick={() => void openAction(value)}>{eventLabels[value] ?? value}</button>)}</div><Timeline loader={() => apiRequest<TimelineEntry[]>(`/api/security-events/${id}/timeline`)} dependencyKey={`${id}-${event.version}`} /></>}{action && <ActionDialog title={eventLabels[action] ?? action} fields={fields} confirmLabel={action === "GRADE_CONFIRM" ? "确认高风险评级" : action === "REVIEW_PASS" ? "确认复核通过" : undefined} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
}

export function InspectionPage() {
  const { session } = useAuth();
  const tasks = useApi(() => apiRequest<ListResult<InspectionTask>>("/api/inspection-tasks"));
  const events = useApi(() => apiRequest<ListResult<SecurityEvent>>("/api/security-events"));
  const [selected, setSelected] = useState<{ kind: "task" | "event"; id: string } | null>(null);
  const [createKind, setCreateKind] = useState<"task" | "event" | null>(null);
  const canCreateTask = session?.actor.roles.includes("MANAGER") ?? false;
  async function create(values: Record<string, string>) {
    if (createKind === "task") {
      const created = await apiRequest<InspectionTask>("/api/inspection-tasks", { method: "POST", idempotencyKey: createIdempotencyKey("inspection-create"), body: { title: values.title, description: values.description, route_points: values.route_points.split(",").map((value) => value.trim()).filter(Boolean), planned_at: null, due_at: null, attachment_ids: [] } });
      setSelected({ kind: "task", id: created.id }); await tasks.reload();
    } else {
      const parameters = { event_type: values.event_type, risk_level: values.risk_level, location: values.location };
      const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "SECURITY_EVENT_CREATE", parameters } });
      const created = await apiRequest<SecurityEvent>("/api/security-events", { method: "POST", idempotencyKey: createIdempotencyKey("security-create"), body: { ...parameters, description: values.description, source_task_id: null, report_source: "MANUAL", attachment_ids: [], confirmation_token: confirmation.token } });
      setSelected({ kind: "event", id: created.id }); await events.reload();
    }
  }
  const createFields: ActionField[] = createKind === "task" ? [{ name: "title", label: "任务标题", required: true }, { name: "description", label: "任务说明", type: "textarea", required: true }, { name: "route_points", label: "路线点位（逗号分隔）", required: true }] : [{ name: "event_type", label: "事件类型", type: "select", options: ["GAS_LEAK", "FIRE", "PERSONAL_SAFETY", "EQUIPMENT_FAULT", "OTHER"].map((value) => ({ value, label: value })) }, { name: "risk_level", label: "风险等级", type: "select", options: ["LOW", "MEDIUM", "HIGH_RISK"].map((value) => ({ value, label: value })) }, { name: "location", label: "发生位置", required: true }, { name: "description", label: "事件描述", type: "textarea", required: true }];
  return <><header className="page-heading"><div><span className="eyebrow">SAFETY OPERATIONS</span><h1>巡检与安防事件</h1><p>巡检任务、异常记录和风险事件分开追踪。</p></div><div className="action-row">{canCreateTask && <button className="button ghost" onClick={() => setCreateKind("task")}><Plus size={16} />新建巡检任务</button>}<button className="button primary" onClick={() => setCreateKind("event")}><Plus size={16} />人工上报事件</button></div></header><div className={selected ? "master-detail" : ""}><div className="two-column"><section className="content-panel"><div className="panel-heading"><h2>巡检任务</h2><span className="entity-icon compact"><ClipboardCheck /></span></div>{tasks.loading ? <Loading /> : tasks.error ? <ErrorState error={tasks.error} retry={() => void tasks.reload()} /> : !tasks.data?.items.length ? <Empty title="暂无巡检任务" /> : <div className="entity-list">{tasks.data.items.map((task) => <button className="entity-card entity-button" key={task.id} onClick={() => setSelected({ kind: "task", id: task.id })}><div className="entity-main"><span className={`status ${task.status.toLowerCase()}`}>{task.status}</span><h3>{task.title}</h3><p>{task.description}</p><small>版本 {task.version}</small></div></button>)}</div>}</section><section className="content-panel"><div className="panel-heading"><h2>安防事件</h2><span className="entity-icon compact danger"><ShieldAlert /></span></div>{events.loading ? <Loading /> : events.error ? <ErrorState error={events.error} retry={() => void events.reload()} /> : !events.data?.items.length ? <Empty title="暂无安防事件" detail="模型不可用时也可通过结构化流程人工上报。" /> : <div className="entity-list">{events.data.items.map((event) => <button className="entity-card entity-button" key={event.id} onClick={() => setSelected({ kind: "event", id: event.id })}><div className="entity-main"><div><span className={`status ${event.status.toLowerCase()}`}>{event.status}</span><span className={`risk ${event.risk_level.toLowerCase()}`}>{event.risk_level}</span></div><h3>{event.location}</h3><p>{event.description}</p><small>{event.event_type} · 版本 {event.version}</small></div></button>)}</div>}</section></div>{selected?.kind === "task" && <TaskDetail id={selected.id} onClose={() => setSelected(null)} onChanged={tasks.reload} />}{selected?.kind === "event" && <EventDetail id={selected.id} onClose={() => setSelected(null)} onChanged={events.reload} />}</div>{createKind && <ActionDialog title={createKind === "task" ? "新建巡检任务" : "人工上报安防事件"} fields={createFields} confirmLabel={createKind === "event" ? "核对并上报" : "创建任务"} onClose={() => setCreateKind(null)} onConfirm={create} />}</>;
}
