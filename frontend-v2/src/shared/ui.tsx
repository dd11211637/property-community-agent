import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import styles from "../styles/ui.module.css";

export function Button({ tone = "secondary", iconOnly = false, className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "secondary" | "ghost" | "danger"; iconOnly?: boolean }) {
  return <button className={`${styles.button} ${styles[tone]} ${iconOnly ? styles.iconButton : ""} ${className}`} {...props} />;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input className={styles.input} {...props} />; }
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea className={`${styles.input} ${styles.textarea}`} {...props} />; }

export function Card({ children, interactive = false, className = "" }: { children: ReactNode; interactive?: boolean; className?: string }) {
  return <section className={`${styles.card} ${interactive ? styles.interactive : ""} ${className}`}>{children}</section>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "dangerTone" | "info" }) {
  return <span className={`${styles.badge} ${tone === "neutral" ? "" : styles[tone]}`}>{children}</span>;
}

export function Tabs({ items, active, onChange }: { items: { id: string; label: string }[]; active: string; onChange(id: string): void }) {
  return <div className={styles.tabs} role="tablist">{items.map((item) => <button key={item.id} role="tab" aria-selected={item.id === active} className={`${styles.tab} ${item.id === active ? styles.tabActive : ""}`} onClick={() => onChange(item.id)}>{item.label}</button>)}</div>;
}

export function LoadingState({ label = "正在加载" }: { label?: string }) { return <div className={styles.state} role="status"><span className={styles.spinner} /><span>{label}</span></div>; }
export function Skeleton({ width = "100%" }: { width?: string }) { return <div className={styles.skeleton} style={{ width }} aria-hidden="true" />; }
export function EmptyState({ title, description }: { title: string; description: string }) { return <div className={styles.state}><Inbox /><strong>{title}</strong><span>{description}</span></div>; }
export function ErrorState({ title = "暂时无法加载", description }: { title?: string; description: string }) { return <div className={styles.state} role="alert"><AlertCircle /><strong>{title}</strong><span>{description}</span></div>; }
export function InlineAlert({ children }: { children: ReactNode }) { return <div className={styles.alert} role="status"><LoaderCircle size={18} />{children}</div>; }
