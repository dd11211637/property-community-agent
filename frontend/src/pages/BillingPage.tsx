import { CircleHelp, ReceiptText, X } from "lucide-react";
import { useState } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { Bill, BillDetail, Consultation } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog } from "../components/ActionDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";
import { StatusBadge } from "../components/StatusBadge";
import { displayDate, displayLabel, displayMoney } from "../ui/display";

function BillDetails({ id, onClose, onConsult }: { id: string; onClose: () => void; onConsult: (billId: string) => void }) {
  const detail = useApi(() => apiRequest<BillDetail>(`/api/billing/bills/${id}`), id);
  return <section className="detail-panel"><div className="panel-heading"><h2>账单详情</h2><button className="icon-button" aria-label="关闭详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : detail.data && <><div className="bill-detail-hero"><div><span>{detail.data.bill.bill_period}</span><strong>¥ {displayMoney(detail.data.bill.total_amount)}</strong><StatusBadge value={detail.data.bill.status} /></div><small>当前房屋账单</small></div><div className="charge-breakdown"><div><span>物业费</span><b>¥ {displayMoney(detail.data.bill.property_fee ?? 0)}</b></div><div><span>公摊水电</span><b>¥ {displayMoney(detail.data.bill.utility_fee ?? 0)}</b></div><div><span>车位费</span><b>¥ {displayMoney(detail.data.bill.parking_fee ?? 0)}</b></div><div><span>滞纳金</span><b>¥ {displayMoney(detail.data.bill.late_fee ?? 0)}</b></div></div><p className="data-freshness">数据更新时间：{displayDate(detail.data.bill.source_time)}</p><h3 className="section-title">费用规则</h3>{detail.data.unknown_rule ? <div className="warning-note"><b>费用依据待核实</b><p>系统不会猜测计算依据，可发起财务咨询。</p></div> : <div className="audience-preview"><b>{detail.data.rule?.name ?? "已关联费用规则"}</b><p>这笔费用已关联社区当前生效的计费规则。如需核对明细，可直接发起咨询。</p></div>}<div className="action-row"><button className="button ghost" onClick={() => onConsult(detail.data!.bill.bill_id)}>对这笔费用有疑问？</button></div></>}</section>;
}

export function BillingPage() {
  const { session } = useAuth();
  const houseId = session?.current_house_id ?? "";
  const bills = useApi(() => apiRequest<Bill[]>("/api/billing/bills"), houseId);
  const consultations = useApi(() => apiRequest<Consultation[]>("/api/billing/consultations"), houseId);
  const [selectedBill, setSelectedBill] = useState<string | null>(null);
  const [consultOpen, setConsultOpen] = useState(false);
  const [consultBill, setConsultBill] = useState<string | null>(null);
  const [selectedConsultation, setSelectedConsultation] = useState<Consultation | null>(null);
  async function createConsultation(values: Record<string, string>) {
    const parameters = { subject: values.subject, description: values.description, bill_id: consultBill };
    const confirmation = await apiRequest<{ token: string }>("/api/confirmations", { method: "POST", body: { action: "CREATE_CONSULTATION", parameters } });
    const created = await apiRequest<Consultation>("/api/billing/consultations", { method: "POST", idempotencyKey: createIdempotencyKey("billing-consultation"), body: { ...parameters, confirmation_token: confirmation.token } });
    setSelectedConsultation(created); await consultations.reload();
  }
  async function transition(action: "submit" | "appeal") {
    if (!selectedConsultation) return;
    const updated = await apiRequest<Consultation>(`/api/billing/consultations/${selectedConsultation.id}/${action}`, {
      method: "POST",
      idempotencyKey: createIdempotencyKey(`consultation-${action}`),
      body: { expected_version: selectedConsultation.version },
    });
    setSelectedConsultation(updated); await consultations.reload();
  }
  const openConsultation = (billId: string | null) => { setConsultBill(billId); setConsultOpen(true); };
  return <><header className="page-heading"><div><span className="eyebrow">我的费用</span><h1>账单费用</h1><p>先看本期应缴与状态，需要时再核对明细和费用依据。</p></div><button className="button ghost" onClick={() => openConsultation(null)}><CircleHelp size={17} />发起财务咨询</button></header><div className={selectedBill ? "master-detail" : ""}><section className="content-panel"><div className="panel-heading"><h2>当前房屋账单</h2><span className="safe-note">支付、退款与改账不在此入口办理</span></div>{bills.loading ? <Loading /> : bills.error ? <ErrorState error={bills.error} retry={() => void bills.reload()} /> : !bills.data?.length ? <Empty title="当前房屋暂无账单" /> : <div className="bill-grid">{bills.data.map((bill, index) => <button className={`bill-card entity-button ${index === 0 ? "current" : ""}`} key={bill.bill_id} onClick={() => setSelectedBill(bill.bill_id)}><div><span className="entity-icon"><ReceiptText /></span><StatusBadge value={bill.status} /></div><small>{displayLabel(bill.fee_type, "社区综合费用")} · {bill.bill_period}</small><strong>¥ {displayMoney(bill.total_amount)}</strong><p>{index === 0 ? "本期账单" : "历史账单"}</p><footer>数据更新时间：{displayDate(bill.source_time)}</footer></button>)}</div>}</section>{selectedBill && <BillDetails id={selectedBill} onClose={() => setSelectedBill(null)} onConsult={(billId) => openConsultation(billId)} />}</div><section className="content-panel admin-health"><div className="panel-heading"><h2>我的财务咨询</h2><button className="text-button" onClick={() => void consultations.reload()}>刷新</button></div>{consultations.loading ? <Loading /> : consultations.error ? <ErrorState error={consultations.error} retry={() => void consultations.reload()} /> : !consultations.data?.length ? <Empty title="暂无财务咨询" detail="对费用有疑问时，可以从账单详情发起咨询。" /> : <div className="entity-list">{consultations.data.map((ticket) => <button className="entity-card entity-button" key={ticket.id} onClick={() => setSelectedConsultation(ticket)}><div className="entity-main"><StatusBadge value={ticket.status} /><h3>{ticket.subject}</h3><p>{ticket.description}</p><small>{ticket.answer ? `财务已答复：${ticket.answer}` : "等待财务答复"}</small></div></button>)}</div>}</section>{selectedConsultation && <section className="detail-panel admin-health"><div className="panel-heading"><h2>咨询详情</h2><button className="icon-button" aria-label="关闭咨询详情" onClick={() => setSelectedConsultation(null)}><X /></button></div><div className="detail-grid"><div><small>处理状态</small><b>{displayLabel(selectedConsultation.status)}</b></div><div><small>关联费用</small><b>{selectedConsultation.bill_id ? "已关联账单" : "综合费用咨询"}</b></div></div><p className="detail-description">{selectedConsultation.description}</p>{selectedConsultation.answer && <div className="audience-preview"><b>财务答复</b><p>{selectedConsultation.answer}</p></div>}<div className="action-row">{selectedConsultation.status === "DRAFT" && <button className="button primary" onClick={() => void transition("submit")}>提交咨询</button>}{selectedConsultation.status === "ANSWERED" && <button className="button ghost" onClick={() => void transition("appeal")}>申诉</button>}</div></section>}{consultOpen && <ActionDialog title="发起财务咨询" fields={[{ name: "subject", label: "咨询主题", required: true, initial: consultBill ? "本期账单费用咨询" : "" }, { name: "description", label: "问题描述", type: "textarea", required: true }]} onClose={() => setConsultOpen(false)} onConfirm={createConsultation} />}</>;
}
