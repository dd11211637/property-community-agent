import { CircleHelp, ReceiptText, X } from "lucide-react";
import { useState } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { Bill, BillDetail, Consultation } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ActionDialog } from "../components/ActionDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

function BillDetails({ id, onClose, onConsult }: { id: string; onClose: () => void; onConsult: (billId: string) => void }) {
  const detail = useApi(() => apiRequest<BillDetail>(`/api/billing/bills/${id}`), id);
  return <section className="detail-panel"><div className="panel-heading"><h2>账单详情</h2><button className="icon-button" aria-label="关闭详情" onClick={onClose}><X /></button></div>{detail.loading ? <Loading /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.reload()} /> : detail.data && <><div className="detail-grid"><div><small>账单编号</small><b>{detail.data.bill.bill_id}</b></div><div><small>状态 / 版本</small><b>{detail.data.bill.status} / v{detail.data.bill.version}</b></div><div><small>账期</small><b>{detail.data.bill.bill_period}</b></div><div><small>总额</small><b>¥ {detail.data.bill.total_amount}</b></div><div><small>物业费</small><b>¥ {detail.data.bill.property_fee ?? 0}</b></div><div><small>公摊水电</small><b>¥ {detail.data.bill.utility_fee ?? 0}</b></div><div><small>车位 / 滞纳</small><b>¥ {detail.data.bill.parking_fee ?? 0} / ¥ {detail.data.bill.late_fee ?? 0}</b></div><div><small>数据时间</small><b>{detail.data.bill.source_time ? new Date(detail.data.bill.source_time).toLocaleString("zh-CN") : "未提供"}</b></div></div><h3 className="section-title">费用规则</h3>{detail.data.unknown_rule ? <div className="warning-note"><b>规则缺失</b><p>系统不会猜测计算依据，可发起财务咨询核实。</p></div> : <div className="audience-preview"><b>{detail.data.rule?.name} · {detail.data.rule?.version}</b><p>{JSON.stringify(detail.data.rule?.parameters ?? {})}</p></div>}<div className="action-row"><button className="button ghost" onClick={() => onConsult(detail.data!.bill.bill_id)}>针对该账单咨询</button></div></>}</section>;
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
  return <><header className="page-heading"><div><span className="eyebrow">BILLING</span><h1>账单费用</h1><p>查看当前房屋账单、费用规则与财务咨询状态。</p></div><button className="button ghost" onClick={() => openConsultation(null)}><CircleHelp size={17} />发起财务咨询</button></header><div className={selectedBill ? "master-detail" : ""}><section className="content-panel"><div className="panel-heading"><h2>当前房屋账单</h2><span className="safe-note">不提供支付、退款、改账或减免操作</span></div>{bills.loading ? <Loading /> : bills.error ? <ErrorState error={bills.error} retry={() => void bills.reload()} /> : !bills.data?.length ? <Empty title="当前房屋暂无账单" /> : <div className="bill-grid">{bills.data.map((bill) => <button className="bill-card entity-button" key={bill.bill_id} onClick={() => setSelectedBill(bill.bill_id)}><div><span className="entity-icon"><ReceiptText /></span><span className={`status ${bill.status.toLowerCase()}`}>{bill.status}</span></div><small>{bill.fee_type ?? "综合费用"} · {bill.bill_period}</small><strong>¥ {bill.total_amount}</strong><p>账单号 {bill.bill_id}</p><footer>规则版本：{bill.rule_version ?? "未知"}<br />数据时间：{bill.source_time ? new Date(bill.source_time).toLocaleString("zh-CN") : "未提供"} · 版本 {bill.version}</footer></button>)}</div>}</section>{selectedBill && <BillDetails id={selectedBill} onClose={() => setSelectedBill(null)} onConsult={(billId) => openConsultation(billId)} />}</div><section className="content-panel admin-health"><div className="panel-heading"><h2>我的财务咨询</h2><button className="text-button" onClick={() => void consultations.reload()}>刷新</button></div>{consultations.loading ? <Loading /> : consultations.error ? <ErrorState error={consultations.error} retry={() => void consultations.reload()} /> : !consultations.data?.length ? <Empty title="暂无财务咨询" /> : <div className="entity-list">{consultations.data.map((ticket) => <button className="entity-card entity-button" key={ticket.id} onClick={() => setSelectedConsultation(ticket)}><div className="entity-main"><span className={`status ${ticket.status.toLowerCase()}`}>{ticket.status}</span><h3>{ticket.subject}</h3><p>{ticket.description}</p><small>{ticket.answer ? `财务答复：${ticket.answer}` : "等待财务答复"}</small></div></button>)}</div>}</section>{selectedConsultation && <section className="detail-panel admin-health"><div className="panel-heading"><h2>咨询详情</h2><button className="icon-button" aria-label="关闭咨询详情" onClick={() => setSelectedConsultation(null)}><X /></button></div><div className="detail-grid"><div><small>状态</small><b>{selectedConsultation.status}</b></div><div><small>关联账单</small><b>{selectedConsultation.bill_id ?? "无"}</b></div></div><p className="detail-description">{selectedConsultation.description}</p>{selectedConsultation.answer && <div className="audience-preview"><b>财务答复</b><p>{selectedConsultation.answer}</p></div>}<div className="action-row">{selectedConsultation.status === "DRAFT" && <button className="button primary" onClick={() => void transition("submit")}>提交咨询</button>}{selectedConsultation.status === "ANSWERED" && <button className="button ghost" onClick={() => void transition("appeal")}>申诉</button>}</div></section>}{consultOpen && <ActionDialog title="发起财务咨询" fields={[{ name: "subject", label: "咨询主题", required: true, initial: consultBill ? `账单 ${consultBill} 费用咨询` : "" }, { name: "description", label: "问题描述", type: "textarea", required: true }]} onClose={() => setConsultOpen(false)} onConfirm={createConsultation} />}</>;
}
