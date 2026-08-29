import { Link } from "react-router-dom";
import {
  AnnouncementCard,
  BillCard,
  InspectionTaskCard,
  SecurityEventCard,
  WorkOrderCard,
} from "../domain/cards";
import { Card } from "../shared/ui";
import styles from "../styles/agent-real.module.css";
import { normalizeAgentFacts } from "./factsModel";

export function AgentFacts({ facts }: { facts: unknown }) {
  const cards = normalizeAgentFacts(facts);
  if (!cards.length)
    return facts ? <Card><strong>结构化结果</strong><p>此结果暂不支持可视化，请通过对应业务页面核对。</p></Card> : null;
  return <div className={styles.factStack}>{cards.map((card) => {
    if (card.type === "work-order") return <Link key={card.id} to={`/repairs/${card.id}`}><WorkOrderCard value={card.value} variant="agent" /></Link>;
    if (card.type === "bill") return <Link key={card.id} to={`/billing/bills/${card.id}`}><BillCard value={card.value} variant="agent" /></Link>;
    if (card.type === "announcement") return <Link key={card.id} to={`/community/announcements/${card.id}`}><AnnouncementCard value={card.value} variant="agent" /></Link>;
    if (card.type === "inspection") return <Link key={card.id} to={`/operations/inspections/${card.id}`}><InspectionTaskCard value={card.value} variant="agent" /></Link>;
    if (card.type === "consultation") return <Link key={card.id} to={`/billing/consultations/${card.id}`}><Card><strong>{card.subject}</strong><p>咨询状态：{card.status}</p></Card></Link>;
    return <Link key={card.id} to={`/operations/security/${card.id}`}><SecurityEventCard value={card.value} variant="agent" /></Link>;
  })}</div>;
}
