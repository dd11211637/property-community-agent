import { CircleAlert, ShieldCheck } from "lucide-react";
import type { PendingConfirmation } from "./models";
import { Button, Card } from "../shared/ui";
import styles from "../styles/agent-real.module.css";

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "未提供";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) return value.map(displayValue).join("、");
  return "结构化参数";
}
export function ConfirmationCard({
  value,
  busy,
  onConfirm,
  onCancel,
}: {
  value: PendingConfirmation;
  busy: boolean;
  onConfirm(): void;
  onCancel(): void;
}) {
  return <Card className={styles.confirmation}>
    <div className={styles.cardHeading}><ShieldCheck /><div><strong>需要确认</strong><p>{value.summary}</p></div></div>
    <dl className={styles.params}>
      {Object.entries(value.params).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{displayValue(item)}</dd></div>)}
    </dl>
    <div className={styles.actions}>
      <Button tone="primary" disabled={busy} onClick={onConfirm}>确认执行</Button>
      <Button disabled={busy} onClick={onCancel}>取消</Button>
    </div>
  </Card>;
}

export function HumanHandoffCard({ ticketId }: { ticketId: string | null }) {
  return <Card className={styles.handover}>
    <div className={styles.cardHeading}><CircleAlert /><div><strong>已转人工处理</strong><p>Agent 不会继续表示自主完成。已确认的业务结果仍然保留。</p></div></div>
    {ticketId ? <span>工单/交接标识：{ticketId}</span> : <span>后端尚未提供人工处理标识。</span>}
  </Card>;
}
