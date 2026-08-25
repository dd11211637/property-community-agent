import { ArrowUp, Bot, Headphones, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { apiRequest, streamAgentTurn } from "../api/client";
import type { AgentConversation, AgentMessage } from "../api/contracts";
import { AgentContextPanel } from "../components/AgentContextPanel";
import { AgentConversationRail } from "../components/AgentConversationRail";
import { ConfirmDialog } from "../components/ConfirmDialog";

type AgentReply = {
  reply: string;
  operation_level?: "read" | "write-low-risk" | "write-high-risk";
  pending_confirmation?: { summary: string; tool: string; params: Record<string, unknown>; action_hash: string };
  slot_prompt?: SlotPrompt;
  facts?: AgentFacts;
  agent_trace?: {
    trace_id: string;
    status: "RUNNING" | "COMPLETED" | "DEGRADED" | "REJECTED";
    finish_reason?: string | null;
    degraded: boolean;
    step_count: number;
  };
  handover_required?: boolean;
};

type SlotPrompt = {
  field: string;
  label: string;
  prompt: string;
  allow_custom: boolean;
  options: Array<{ label: string; value: unknown }>;
  help_text?: string;
  step?: number;
  total_steps?: number;
  completed?: Array<{ field: string; label: string; value: string }>;
};

type AgentFacts = {
  count?: number;
  items?: Array<Record<string, unknown>>;
  work_order?: Record<string, unknown>;
  timeline?: Array<Record<string, unknown>>;
  bill?: Record<string, unknown>;
  task?: Record<string, unknown>;
  event?: Record<string, unknown>;
  total?: number;
  completed?: number;
  incomplete?: number;
  status_counts?: Record<string, number>;
};

type ChatMessage = { role: "user" | "assistant"; text: string; facts?: AgentFacts };

const confirmationLabels: Record<string, string> = {
  category: "问题类型",
  location: "具体位置",
  description: "问题描述",
  urgency: "紧急程度",
  subject: "咨询主题",
  title: "标题",
  body: "公告正文",
  audience: "公告受众",
  scheduled_at: "发布时间",
  point: "巡检点位",
  finding: "发现的问题",
  note: "记录内容",
  event_type: "事件类型",
  risk_level: "风险等级",
  record_type: "记录类型",
};

const confirmationValues: Record<string, string> = {
  WATER_PLUMBING: "水管及排水",
  ELECTRICAL: "电气问题",
  ELEVATOR: "电梯问题",
  OTHER: "其他问题",
  NORMAL: "普通",
  URGENT: "紧急",
  HIGH_RISK: "高风险",
  LOW: "低风险",
  MEDIUM: "中风险",
  GAS_LEAK: "燃气泄漏",
  FIRE: "火情",
  PERSONAL_SAFETY: "人员安全",
  EQUIPMENT_FAULT: "设施设备隐患",
  POINT_RECORD: "点位记录",
  PROGRESS: "过程记录",
  COMPLETION: "完成记录",
  GENERAL: "一般通知",
  MAINTENANCE: "维护通知",
  SAFETY: "安全通知",
  EMERGENCY: "紧急通知",
};

function confirmationEntries(params: Record<string, unknown>, tool?: string) {
  return Object.entries(params)
    .filter(([key]) => key in confirmationLabels)
    .map(([key, value]) => ({
      key,
      label: key === "category" && tool?.startsWith("announcement_") ? "公告分类" : confirmationLabels[key],
      value: key === "audience" && typeof value === "object" && value !== null && Object.keys(value).length === 0
        ? "全社区"
        : confirmationValues[String(value)] ?? (typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)),
    }));
}

function currentHouseId(): string | null {
  return sessionStorage.getItem("property_agent_house_id");
}

function storedConversationId(): string | null {
  return sessionStorage.getItem("property_agent_conversation_id");
}

const statusLabels: Record<string, string> = {
  PENDING_ASSIGNMENT: "等待物业派单",
  PENDING_ACCEPTANCE: "等待维修人员接单",
  PROCESSING: "正在处理中",
  PENDING_VERIFICATION: "等待住户验收",
  REWORKING: "正在返工",
  CLOSED: "已完成",
  UNPAID: "待缴费",
  OVERDUE: "已逾期",
  PAID: "已缴费",
  CANCELLED: "已取消",
  PLANNED: "待分派",
  ASSIGNED: "已分派",
  IN_PROGRESS: "巡检中",
  SUBMITTED: "待复核",
  COMPLETED: "已完成",
  REPORTED: "待分派",
  PENDING_REVIEW: "待复核",
};

function statusLabel(value: unknown) {
  const text = String(value ?? "");
  return statusLabels[text] ?? text;
}

function money(value: unknown) {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

function AgentFactsCard({ facts }: { facts: AgentFacts }) {
  if (facts.work_order) {
    const order = facts.work_order;
    const latest = facts.timeline?.at(-1);
    const latestDetail = latest?.note ?? latest?.reason;
    return <div className="agent-facts"><b>工单 {String(order.business_no ?? order.id ?? "")}</b><span>{statusLabel(order.status)}</span><p>{String(order.location ?? "")}</p>{latestDetail != null && <small>最新进展：{String(latestDetail)}</small>}</div>;
  }
  if (facts.task) {
    const task = facts.task;
    return <div className="agent-facts"><b>巡检任务 {String(task.business_no ?? "")}</b><span>{statusLabel(task.status)}</span><p>{String(task.title ?? "")}</p><small>{Array.isArray(task.route_points) ? `路线：${task.route_points.join("、")}` : ""}</small></div>;
  }
  if (facts.event) {
    const event = facts.event;
    return <div className="agent-facts"><b>安防事件 {String(event.business_no ?? "")}</b><span>{statusLabel(event.status)}</span><p>{String(event.location ?? "")}</p><small>{confirmationValues[String(event.event_type)] ?? String(event.event_type ?? "")} · {confirmationValues[String(event.risk_level)] ?? String(event.risk_level ?? "")}</small></div>;
  }
  const items = facts.items ?? [];
    if (items.length) {
      return <div className="agent-fact-list">{facts.total != null && <div className="agent-facts"><b>巡检完成情况</b><span>{facts.incomplete === 0 ? "全部完成" : `${facts.incomplete} 项未完成`}</span><p>共 {facts.total} 项，已完成 {facts.completed ?? 0} 项</p></div>}{items.slice(0, 5).map((item, index) => {
        const type = String(item.entity_type ?? "");
        if (type === "BILL" || item.bill_id) return <div className="agent-facts" key={String(item.bill_id)}><b>{String(item.period ?? "账单")} · ¥{money(item.total_amount ?? item.amount)}</b><span>{statusLabel(item.status)}</span><p>物业费 ¥{money(item.property_fee)} · 水电费 ¥{money(item.utility_fee)} · 停车费 ¥{money(item.parking_fee)}</p><small>账单号 {String(item.bill_id)}</small></div>;
        if (type === "INSPECTION_TASK") return <div className="agent-facts" key={String(item.id ?? index)}><b>{String(item.title ?? "巡检任务")}</b><span>{statusLabel(item.status)}</span><p>{Array.isArray(item.route_points) ? item.route_points.join("、") : ""}</p><small>任务号 {String(item.business_no ?? "")}</small></div>;
        if (type === "SECURITY_EVENT") return <div className="agent-facts" key={String(item.id ?? index)}><b>{String(item.location ?? "安防事件")}</b><span>{statusLabel(item.status)}</span><p>{String(item.description ?? "")}</p><small>{confirmationValues[String(item.event_type)] ?? String(item.event_type ?? "")} · {confirmationValues[String(item.risk_level)] ?? String(item.risk_level ?? "")}</small></div>;
        if (type === "ANNOUNCEMENT") return <div className="agent-facts" key={String(item.id ?? index)}><b>{String(item.title)}</b><span>{statusLabel(item.status)}</span><p>{String(item.body ?? "")}</p><small>{item.published_at ? `发布时间 ${String(item.published_at)}` : "社区公告"}</small></div>;
        return <div className="agent-facts" key={String(item.id ?? index)}><b>{String(item.business_no ?? "业务记录")}</b><span>{statusLabel(item.status)}</span><p>{String(item.location ?? "")}</p></div>;
      })}</div>;
    }
  return null;
}

export function HomePage() {
  const [activeConversationId, setActiveConversationId] = useState(
    () => storedConversationId() ?? crypto.randomUUID(),
  );
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", text: "你好，我是社区服务助手。你可以查询账单、发起报修，或了解社区服务。" },
  ]);
  const [pending, setPending] = useState(false);
  const [action, setAction] = useState<AgentReply["pending_confirmation"]>();
  const [slotPrompt, setSlotPrompt] = useState<SlotPrompt>();
  useEffect(() => {
    const existing = storedConversationId();
    if (!existing || existing !== activeConversationId) return;
    let active = true;
    void Promise.all([
      apiRequest<AgentMessage[]>(`/api/agent/conversations/${existing}/messages`),
      apiRequest<AgentReply>(`/api/agent/conversations/${existing}`),
    ]).then(([history, status]) => {
        if (!active) return;
        setMessages(history.map((item) => ({ role: item.role === "user" ? "user" : "assistant", text: item.content })));
        setAction(status.pending_confirmation);
      })
      .catch(() => {
        // A missing/expired conversation must not make the structured home page unusable.
        sessionStorage.removeItem("property_agent_conversation_id");
      });
    return () => { active = false; };
  }, [activeConversationId]);
  async function sendAgent(text: string, slots?: Record<string, unknown>) {
    if (!text || pending) return;
    setMessages((items) => [...items, { role: "user", text }]);
    setSlotPrompt(undefined);
    setPending(true);
    sessionStorage.setItem("property_agent_conversation_id", activeConversationId);
    try {
      const reply = await streamAgentTurn<AgentReply>(
        `/api/agent/conversations/${activeConversationId}/messages/stream`,
        { text, house_id: currentHouseId(), slots },
      );
      if (!reply.slot_prompt && !reply.pending_confirmation && (reply.reply.trim() || reply.facts)) {
        setMessages((items) => [...items, { role: "assistant", text: reply.reply, facts: reply.facts }]);
      }
      setSlotPrompt(reply.slot_prompt);
      setAction(reply.pending_confirmation);
      setHistoryRefresh((value) => value + 1);
    } catch (reason) {
      setMessages((items) => [...items, { role: "assistant", text: reason instanceof Error ? reason.message : "服务暂时不可用。" }]);
    } finally { setPending(false); }
  }
  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    void sendAgent(text, slotPrompt ? { [slotPrompt.field]: text } : undefined);
  };
  async function resolveConfirmation(confirmed: boolean) {
    if (!action) return;
    const reply = await apiRequest<AgentReply>(`/api/agent/conversations/${activeConversationId}/confirmations`, {
      method: "POST",
      body: { confirmed, action_hash: action.action_hash },
    });
    if (reply.reply.trim() || reply.facts) {
      setMessages((items) => [...items, { role: "assistant", text: reply.reply, facts: reply.facts }]);
    }
    setAction(undefined);
    setHistoryRefresh((value) => value + 1);
  }
  async function cancelConfirmation() {
    try {
      await resolveConfirmation(false);
    } catch (reason) {
      setMessages((items) => [...items, { role: "assistant", text: reason instanceof Error ? reason.message : "取消操作失败，请稍后重试。" }]);
    }
  }
  function startNewConversation() {
    const next = crypto.randomUUID();
    sessionStorage.setItem("property_agent_conversation_id", next);
    setActiveConversationId(next);
    setMessages([{ role: "assistant", text: "新的对话已准备好。告诉我你想查询或办理什么。" }]);
    setAction(undefined);
    setSlotPrompt(undefined);
  }
  function selectConversation(conversation: AgentConversation) {
    sessionStorage.setItem("property_agent_conversation_id", conversation.conversation_id);
    setActiveConversationId(conversation.conversation_id);
  }
  return (
    <div className="agent-workspace">
      <AgentConversationRail activeId={activeConversationId} refreshKey={historyRefresh} onNew={startNewConversation} onSelect={selectConversation} />
      <section className="agent-panel agent-main">
        <div className="panel-title"><span className="bot-orb"><Bot /></span><div><h1>今天想处理什么？</h1><p><span className="online-dot" /> 社区 Agent 可以查询、办理并持续跟进社区事务</p></div><button className="button ghost human-handoff"><Headphones size={16} />转人工</button><span className="ai-label"><Sparkles size={14} /> Agent 在线</span></div>
        {messages.length === 1 && <div className="agent-suggestions">
          <span>你可以直接这样说</span>
          <button type="button" onClick={() => void sendAgent("我家厨房漏水，需要报修")}>帮我报修</button>
          <button type="button" onClick={() => void sendAgent("查询本月账单")}>查询本月账单</button>
          <button type="button" onClick={() => void sendAgent("今天有停水通知吗？")}>查看停水通知</button>
        </div>}
        <div className="chat-log" aria-live="polite">
          {messages.map((message, index) => <div className={`message ${message.role}`} key={index}>{message.text && <span>{message.text}</span>}{message.facts && <AgentFactsCard facts={message.facts} />}</div>)}
          {pending && <div className="message assistant">正在查询真实业务状态…</div>}
          {slotPrompt && !pending && <div className="slot-prompt">
            {slotPrompt.step && slotPrompt.total_steps && <span className="slot-progress">信息补充 {slotPrompt.step}/{slotPrompt.total_steps}</span>}
            {slotPrompt.completed && slotPrompt.completed.length > 0 && <dl className="slot-completed">{slotPrompt.completed.map((item) => <div key={item.field}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
            <b>{slotPrompt.prompt}</b>
            <div className="slot-options">{slotPrompt.options.map((option) => <button type="button" key={`${option.label}-${option.value}`} onClick={() => void sendAgent(option.label, { [slotPrompt.field]: option.value })}>{option.label}</button>)}</div>
            {(slotPrompt.help_text || slotPrompt.allow_custom) && <small>{slotPrompt.help_text || `也可以在下方输入框中直接填写${slotPrompt.label}。`}</small>}
          </div>}
        </div>
        <form className="chat-input" onSubmit={send}><input value={input} onChange={(e) => setInput(e.target.value)} placeholder="例如：我家厨房水管漏水，想报修" aria-label="发送给社区智能体" /><button aria-label="发送" disabled={pending}><ArrowUp /></button></form>
        <small className="agent-disclaimer">AI 可能出错；费用、状态和操作结果以后端业务记录为准。</small>
      </section>
      <AgentContextPanel />
      {action && <ConfirmDialog title={action.summary} summary={<dl className="summary-list">{confirmationEntries(action.params, action.tool).map(({ key, label, value }) => <div key={key}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>} onClose={() => setAction(undefined)} onCancel={() => void cancelConfirmation()} onConfirm={() => resolveConfirmation(true)} />}
    </div>
  );
}
