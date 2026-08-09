import { ArrowUp, Bot, ChevronRight, Headphones, ReceiptText, ShieldAlert, Sparkles, Wrench } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { apiRequest } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";

type AgentReply = {
  reply: string;
  operation_level?: "read" | "write-low-risk" | "write-high-risk";
  pending_confirmation?: { summary: string; params: Record<string, unknown>; action_hash: string };
  handover_required?: boolean;
};

function conversationId(): string {
  const key = "property_agent_conversation_id";
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const created = crypto.randomUUID();
  sessionStorage.setItem(key, created);
  return created;
}

export function HomePage() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<{ role: "user" | "assistant"; text: string }[]>([
    { role: "assistant", text: "你好，我是社区服务助手。你可以查询账单、发起报修，或了解社区服务。" },
  ]);
  const [pending, setPending] = useState(false);
  const [action, setAction] = useState<AgentReply["pending_confirmation"]>();
  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || pending) return;
    setMessages((items) => [...items, { role: "user", text }]);
    setInput(""); setPending(true);
    try {
      const reply = await apiRequest<AgentReply>(`/api/agent/conversations/${conversationId()}/messages`, { method: "POST", body: { text } });
      setMessages((items) => [...items, { role: "assistant", text: reply.reply }]);
      setAction(reply.pending_confirmation);
    } catch (reason) {
      setMessages((items) => [...items, { role: "assistant", text: reason instanceof Error ? reason.message : "服务暂时不可用。" }]);
    } finally { setPending(false); }
  };
  return (
    <>
      <header className="page-heading"><div><span className="eyebrow">上午好</span><h1>今天想处理什么？</h1><p>从常用服务开始，或直接告诉智能体你的需求。</p></div><button className="button ghost"><Headphones size={17} />转人工服务</button></header>
      <section className="quick-grid">
        <Link to="/repairs" className="quick-card coral"><Wrench /><div><b>我要报修</b><span>提交问题并跟踪进度</span></div><ChevronRight /></Link>
        <Link to="/billing" className="quick-card amber"><ReceiptText /><div><b>查看账单</b><span>查询费用明细与规则</span></div><ChevronRight /></Link>
        <Link to="/inspection" className="quick-card blue"><ShieldAlert /><div><b>巡检事件</b><span>任务记录与风险处置</span></div><ChevronRight /></Link>
      </section>
      <section className="agent-panel">
        <div className="panel-title"><span className="bot-orb"><Bot /></span><div><h2>社区智能体</h2><p><span className="online-dot" /> 服务在线</p></div><span className="ai-label"><Sparkles size={14} /> AI 辅助</span></div>
        <div className="chat-log" aria-live="polite">
          {messages.map((message, index) => <div className={`message ${message.role}`} key={index}>{message.text}</div>)}
          {pending && <div className="message assistant">正在查询真实业务状态…</div>}
        </div>
        <form className="chat-input" onSubmit={send}><input value={input} onChange={(e) => setInput(e.target.value)} placeholder="例如：我家厨房水管漏水，想报修" aria-label="发送给社区智能体" /><button aria-label="发送" disabled={pending}><ArrowUp /></button></form>
        <small className="agent-disclaimer">AI 可能出错；费用、状态和操作结果以后端业务记录为准。</small>
      </section>
      {action && <ConfirmDialog title={action.summary} summary={<dl className="summary-list">{Object.entries(action.params).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>} onClose={() => setAction(undefined)} onConfirm={async () => { await apiRequest(`/api/agent/conversations/${conversationId()}/confirmations`, { method: "POST", body: { confirmed: true, action_hash: action.action_hash } }); }} />}
    </>
  );
}
