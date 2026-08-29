import { ArrowUp, Bot, CheckCircle2, Headphones, Sparkles } from "lucide-react";
import type { StructuredAgentResult } from "../models/viewModels";
import { AnnouncementCard, BillCard, InspectionTaskCard, SecurityEventCard, WorkOrderCard } from "../domain/cards";
import styles from "../styles/agent.module.css";
import { Button, Card } from "../shared/ui";

export function AgentComposer({ placeholder = "描述你的问题，我会结合当前房屋为你处理" }: { placeholder?: string }) {
  return <div className={styles.composer}><textarea aria-label="发送给社区智能体" placeholder={placeholder} /><Button tone="primary" iconOnly aria-label="发送"><ArrowUp size={18} /></Button></div>;
}

export function MessageBubble({ sender, children }: { sender: "assistant" | "user"; children: React.ReactNode }) {
  return <div className={`${styles.message} ${styles[sender]}`}><span className="sr-only">{sender === "assistant" ? "智能助手" : "我"}</span><div className={styles.bubble}>{children}</div></div>;
}

export function SuggestedAction({ label, description }: { label: string; description: string }) {
  return <div className={styles.action}><div><strong>{label}</strong><p>{description}</p></div><Button tone="secondary">查看</Button></div>;
}

export function ConfirmationCard({ title, description, confirmLabel }: { title: string; description: string; confirmLabel: string }) {
  return <Card className={styles.confirmation}><h3>{title}</h3><p>{description}</p><div className={styles.actions}><Button tone="ghost">暂不处理</Button><Button tone="primary"><CheckCircle2 size={17} />{confirmLabel}</Button></div></Card>;
}

export function HumanHandoffCard({ title, owner, status }: { title: string; owner: string; status: string }) {
  return <div className={styles.handoff}><Headphones /><div><strong>{title}</strong><p>{owner} · {status}</p></div></div>;
}

export function ThinkingIndicator() { return <div className={styles.action} role="status"><Sparkles size={18} /><span>正在整理当前上下文…</span></div>; }

export function StructuredResultRenderer({ result }: { result: StructuredAgentResult }) {
  switch (result.type) {
    case "text": return <MessageBubble sender="assistant">{result.text}</MessageBubble>;
    case "work-order": return <WorkOrderCard value={result.value} variant="agent" />;
    case "bill": return <BillCard value={result.value} variant="agent" />;
    case "announcement": return <AnnouncementCard value={result.value} variant="agent" />;
    case "inspection": return <InspectionTaskCard value={result.value} variant="agent" />;
    case "security-event": return <SecurityEventCard value={result.value} variant="agent" />;
    case "suggested-action": return <SuggestedAction label={result.label} description={result.description} />;
    case "confirmation": return <ConfirmationCard title={result.title} description={result.description} confirmLabel={result.confirmLabel} />;
    case "handoff": return <HumanHandoffCard title={result.title} owner={result.owner} status={result.status} />;
  }
}

export function AgentWorkspace({ results }: { results: StructuredAgentResult[] }) {
  return <div className={styles.workspace}><MessageBubble sender="user">帮我看一下家里的报修和本月账单。</MessageBubble><MessageBubble sender="assistant"><Bot size={16} /> 已结合当前房屋整理好重点事项。</MessageBubble>{results.map((result, index) => <StructuredResultRenderer key={`${result.type}-${index}`} result={result} />)}<AgentComposer /></div>;
}
