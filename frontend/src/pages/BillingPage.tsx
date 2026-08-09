import { CircleHelp, ReceiptText } from "lucide-react";
import { apiRequest, queryString } from "../api/client";
import type { Bill, ListResult } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function BillingPage() {
  const { session } = useAuth();
  const houseId = session?.current_house_id ?? "";
  const { data, error, loading, reload } = useApi(() => apiRequest<ListResult<Bill>>(`/api/bills${queryString({ house_id: houseId })}`), houseId);
  return <><header className="page-heading"><div><span className="eyebrow">BILLING</span><h1>账单费用</h1><p>仅展示当前房屋的真实账单来源与更新时间。</p></div><button className="button ghost"><CircleHelp size={17} />发起财务咨询</button></header><section className="content-panel"><div className="panel-heading"><h2>当前房屋账单</h2><span className="safe-note">AI 不会修改金额或承诺减免</span></div>{loading ? <Loading /> : error ? <ErrorState error={error} retry={() => void reload()} /> : !data?.items.length ? <Empty title="当前房屋暂无账单" /> : <div className="bill-grid">{data.items.map((bill) => <article className="bill-card" key={bill.id}><div><span className="entity-icon"><ReceiptText /></span><span className={`status ${bill.payment_status.toLowerCase()}`}>{bill.payment_status}</span></div><small>{bill.fee_type} · {bill.period_start} 至 {bill.period_end}</small><strong>¥ {bill.amount}</strong><p>账单号 {bill.external_bill_no}</p><footer>来源：{bill.source_system}<br />更新：{new Date(bill.source_updated_at).toLocaleString("zh-CN")}</footer></article>)}</div>}</section></>;
}
