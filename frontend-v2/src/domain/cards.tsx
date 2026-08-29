import { CalendarDays, CircleAlert, Clock3, Home, MapPin, ReceiptText, UserRound, Wrench } from "lucide-react";
import type { AnnouncementCardModel, BillCardModel, CardVariant, HouseCardModel, InspectionTaskCardModel, ResidentCardModel, SecurityEventCardModel, WorkOrderCardModel } from "./cardModels";
import { formatCurrency, formatDate, labelFor, statusTone } from "../presentation/format";
import styles from "../styles/domain.module.css";
import { Badge, Card } from "../shared/ui";

function cardClass(variant: CardVariant): string { return `${styles.card} ${variant === "default" ? "" : styles[variant]}`; }

export function WorkOrderCard({ value, variant = "default" }: { value: WorkOrderCardModel; variant?: CardVariant }) {
  return <Card interactive className={cardClass(variant)}><div className={styles.topline}><span className={styles.eyebrow}>{value.number}</span><Badge tone={statusTone(value.status)}>{labelFor(value.status)}</Badge></div><h3 className={styles.title}>{value.title}</h3><p className={styles.summary}>{value.summary}</p><div className={styles.meta}><span><MapPin size={14} />{value.location}</span><span><Wrench size={14} />{labelFor(value.priority)}</span><span><Clock3 size={14} />{formatDate(value.updatedAt)}</span></div></Card>;
}

export function BillCard({ value, variant = "default" }: { value: BillCardModel; variant?: CardVariant }) {
  return <Card interactive className={cardClass(variant)}><div className={styles.topline}><span className={styles.eyebrow}>{value.period} 账单</span><Badge tone={statusTone(value.status)}>{labelFor(value.status)}</Badge></div><p className={styles.amount}>{formatCurrency(value.total)}</p><div className={styles.items}>{value.items.map((item) => <Badge key={item}>{item}</Badge>)}</div><div className={styles.meta}><span><CalendarDays size={14} />缴费截止 {value.dueDate}</span></div></Card>;
}

export function AnnouncementCard({ value, variant = "default", compact = false }: { value: AnnouncementCardModel; variant?: CardVariant; compact?: boolean }) {
  return <Card interactive className={cardClass(compact ? "compact" : variant)}><div className={styles.topline}><Badge tone="info">{labelFor(value.category)}</Badge><Badge tone={statusTone(value.status)}>{labelFor(value.status)}</Badge></div><h3 className={styles.title}>{value.title}</h3><p className={styles.summary}>{value.summary}</p><div className={styles.meta}><span><UserRound size={14} />{value.audience}</span><span><Clock3 size={14} />{formatDate(value.publishedAt)}</span></div></Card>;
}

export function ResidentCard({ value, variant = "default" }: { value: ResidentCardModel; variant?: CardVariant }) {
  return <Card className={cardClass(variant)}><div className={styles.identity}><span className={styles.avatar}>{value.name.slice(0, 1)}</span><div><h3 className={styles.title}>{value.name}</h3><span className={styles.summary}>{value.house}</span></div></div><div className={styles.items}>{value.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}</div><span className={styles.summary}>{value.contact}</span></Card>;
}

export function HouseCard({ value, variant = "default" }: { value: HouseCardModel; variant?: CardVariant }) {
  return <Card className={cardClass(variant)}><div className={styles.topline}><Home /><Badge>{value.occupancy}</Badge></div><h3 className={styles.title}>{value.label}</h3><p className={styles.summary}>{value.address}</p></Card>;
}

export function InspectionTaskCard({ value, variant = "default" }: { value: InspectionTaskCardModel; variant?: CardVariant }) {
  return <Card interactive className={cardClass(variant)}><div className={styles.topline}><span className={styles.eyebrow}>巡检任务</span><Badge tone={statusTone(value.status)}>{labelFor(value.status)}</Badge></div><h3 className={styles.title}>{value.title}</h3><div className={styles.progress} aria-label={`完成度 ${value.progress}%`}><span style={{ width: `${value.progress}%` }} /></div><div className={styles.meta}><span><UserRound size={14} />{value.assignee}</span><span><CalendarDays size={14} />{value.dueAt}</span></div></Card>;
}

export function SecurityEventCard({ value, variant = "default" }: { value: SecurityEventCardModel; variant?: CardVariant }) {
  return <Card interactive className={cardClass(variant)}><div className={styles.topline}><CircleAlert /><Badge tone={statusTone(value.risk)}>{labelFor(value.risk)}</Badge></div><h3 className={styles.title}>{value.title}</h3><div className={styles.meta}><span><MapPin size={14} />{value.location}</span><span><ReceiptText size={14} />{labelFor(value.status)}</span><span><Clock3 size={14} />{formatDate(value.reportedAt)}</span></div></Card>;
}
