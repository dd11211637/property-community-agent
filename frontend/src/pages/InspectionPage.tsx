import { ClipboardCheck, Plus, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { InspectionTask, ListResult, SecurityEvent, StaffOption, TimelineEntry } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog, type ActionField } from "../components/ActionDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";
import { StatusBadge } from "../components/StatusBadge";
import { businessReference, displayDate, displayLabel } from "../ui/display";
import { workspaceFor } from "../ui/roles";

const taskLabels: Record<string, string> = { ASSIGN: "分派", START: "开始巡检", SUBMIT_RECORDS: "提交记录", ADD_RECORD: "追加记录", COMPLETE: "复核完成" };
const eventLabels: Record<string, string> = { ASSIGN: "分派处置", SUBMIT_DISPOSAL: "提交处置", GRADE_CONFIRM: "确认高风险评级", REVIEW_PASS: "复核通过", RETURN: "退回处置" };
const recordOptions = [{ value: "CHECKIN", label: "到场签到" }, { value: "POINT_RECORD", label: "点位记录" }, { value: "PROGRESS", label: "处理进展" }, { value: "COMPLETION", label: "完成记录" }];

function Timeline({ loader, dependencyKey }: { loader: () => Promise<TimelineEntry[]>; dependencyKey: string }) {
  const timeline = useApi(loader, dependencyKey);
  return <><h3 className="section-title">处理时间线</h3>{timeline.loading ? <Loading /> : timeline.error ? <ErrorState error={timeline.error} retry={() => void timeline.reload()} /> : !timeline.data?.length ? <Empty title="暂无时间线记录" /> : <div className="timeline">{timeline.data.map((item, index) => <div key={`${item.created_at}-${index}`}><span /><div><b>{taskLabels[item.action] ?? eventLabels[item.action] ?? "业务进展"}</b><p>{displayLabel(item.from_status, "已创建")} → {displayLabel(item.to_status, "已记录")}</p>{(item.reason || item.note) && <small>{item.reason ?? item.note}</small>}<time>{displayDate(item.created_at)}</time></div></div>)}</div>}</>;
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
  return <section className="detail-panel"><div className="panel-heading"><div><span className="safe-note">{task ? businessReference(task.business_no, "巡检任务") : "巡检任务"}</span><h2>巡检任务详情</h2></div><button className="icon-button" aria-label="关闭任务详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : task && <><div className="repair-detail-summary"><StatusBadge value={task.status} /><h3>{task.title}</h3><p>{task.description}</p><small>路线：{task.route_points?.join("、") ?? "待安排"} · {task.assignee_id ? "已安排执行人员" : "等待安排人员"}</small></div><div className="action-row primary-actions">{task.available_actions?.map((value) => <button className="button ghost" key={value} onClick={() => void openAction(value)}>{taskLabels[value] ?? "继续处理"}</button>)}</div><Timeline loader={() => apiRequest<TimelineEntry[]>(`/api/inspection-tasks/${id}/timeline`)} dependencyKey={`${id}-${task.version}`} /></>}{action && <ActionDialog title={taskLabels[action] ?? "继续处理"} fields={fields} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
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
  return <section className="detail-panel"><div className="panel-heading"><div><span className="safe-note">{event ? businessReference(event.business_no, "安防事件") : "安防事件"}</span><h2>安防事件详情</h2></div><button className="icon-button" aria-label="关闭事件详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : event && <><div className="repair-detail-summary"><div className="action-row"><StatusBadge value={event.risk_level} /><StatusBadge value={event.status} /></div><h3>{event.location}</h3><p>{event.description}</p><small>{displayLabel(event.event_type, "社区安全事件")} · {event.assignee_id ? "已安排处理人员" : "等待安排处理人员"}</small></div>{event.risk_level === "HIGH_RISK" && !event.grade_confirmed_by && <div className="warning-note">高风险事件必须先由授权管理者确认评级，不能直接关闭。</div>}<div className="action-row primary-actions">{event.available_actions?.map((value) => <button className="button ghost" key={value} onClick={() => void openAction(value)}>{eventLabels[value] ?? "继续处理"}</button>)}</div><Timeline loader={() => apiRequest<TimelineEntry[]>(`/api/security-events/${id}/timeline`)} dependencyKey={`${id}-${event.version}`} /></>}{action && <ActionDialog title={eventLabels[action] ?? "继续处理"} fields={fields} confirmLabel={action === "GRADE_CONFIRM" ? "确认高风险评级" : action === "REVIEW_PASS" ? "确认复核通过" : undefined} onClose={() => setAction(null)} onConfirm={execute} />}</section>;
}

export function InspectionPage() {
  const { session } = useAuth();
  const tasks = useApi(() => apiRequest<ListResult<InspectionTask>>("/api/inspection-tasks"));
  const events = useApi(() => apiRequest<ListResult<SecurityEvent>>("/api/security-events"));
  const [selected, setSelected] = useState<{ kind: "task" | "event"; id: string } | null>(null);
  const [createKind, setCreateKind] = useState<"task" | "event" | null>(null);
  const canCreateTask = session?.actor.roles.includes("MANAGER") ?? false;
  const workspace = workspaceFor(session?.actor.roles);
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
  const createFields: ActionField[] = createKind === "task" ? [{ name: "title", label: "任务标题", required: true }, { name: "description", label: "任务说明", type: "textarea", required: true }, { name: "route_points", label: "路线点位（逗号分隔）", required: true }] : [{ name: "event_type", label: "事件类型", type: "select", options: [{ value: "GAS_LEAK", label: "燃气泄漏" }, { value: "FIRE", label: "火情" }, { value: "PERSONAL_SAFETY", label: "人员安全" }, { value: "EQUIPMENT_FAULT", label: "设施设备隐患" }, { value: "OTHER", label: "其他事件" }] }, { name: "risk_level", label: "风险等级", type: "select", options: [{ value: "LOW", label: "低风险" }, { value: "MEDIUM", label: "中风险" }, { value: "HIGH_RISK", label: "高风险" }] }, { name: "location", label: "发生位置", required: true }, { name: "description", label: "事件描述", type: "textarea", required: true }];
  return <><header className="page-heading"><div><span className="eyebrow">{workspace === "resident" ? "社区安全" : "现场运营"}</span><h1>{workspace === "resident" ? "安全事件上报" : "巡检与安防"}</h1><p>{workspace === "resident" ? "发现安全隐患时可在这里核对并上报。" : "先处理异常，再推进日常巡检任务。"}</p></div><div className="action-row">{canCreateTask && <button className="button ghost" onClick={() => setCreateKind("task")}><Plus size={16} />新建巡检任务</button>}<button className="button primary" onClick={() => setCreateKind("event")}><Plus size={16} />人工上报事件</button></div></header><div className={selected ? "master-detail" : ""}><div className={workspace === "resident" ? "" : "two-column"}>{workspace !== "resident" && <section className="content-panel"><div className="panel-heading"><h2>{workspace === "admin" ? "巡检任务概览" : "我的巡检任务"}</h2><span className="entity-icon compact"><ClipboardCheck /></span></div>{tasks.loading ? <Loading /> : tasks.error ? <ErrorState error={tasks.error} retry={() => void tasks.reload()} /> : !tasks.data?.items.length ? <Empty title="暂无巡检任务" /> : <div className="entity-list">{tasks.data.items.map((task) => <button className="entity-card entity-button" key={task.id} onClick={() => setSelected({ kind: "task", id: task.id })}><div className="entity-main"><StatusBadge value={task.status} /><h3>{task.title}</h3><p>{task.description}</p><small>{businessReference(task.business_no, "巡检任务")}</small></div></button>)}</div>}</section>}<section className="content-panel"><div className="panel-heading"><h2>{workspace === "resident" ? "我的上报记录" : "安防异常"}</h2><span className="entity-icon compact danger"><ShieldAlert /></span></div>{events.loading ? <Loading /> : events.error ? <ErrorState error={events.error} retry={() => void events.reload()} /> : !events.data?.items.length ? <Empty title="暂无安防事件" detail="发现隐患时可通过上方入口人工上报。" /> : <div className="entity-list">{events.data.items.map((event) => <button className="entity-card entity-button" key={event.id} onClick={() => setSelected({ kind: "event", id: event.id })}><div className="entity-main"><div><StatusBadge value={event.status} /><StatusBadge value={event.risk_level} /></div><h3>{event.location}</h3><p>{event.description}</p><small>{displayLabel(event.event_type, "社区安全事件")}</small></div></button>)}</div>}</section></div>{selected?.kind === "task" && <TaskDetail id={selected.id} onClose={() => setSelected(null)} onChanged={tasks.reload} />}{selected?.kind === "event" && <EventDetail id={selected.id} onClose={() => setSelected(null)} onChanged={events.reload} />}</div>{createKind && <ActionDialog title={createKind === "task" ? "新建巡检任务" : "人工上报安防事件"} fields={createFields} confirmLabel={createKind === "event" ? "核对并上报" : "创建任务"} onClose={() => setCreateKind(null)} onConfirm={create} />}</>;
}
