import { CircleHelp, ReceiptText } from "lucide-react";
import { apiRequest } from "../api/client";
import type { Bill } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function BillingPage() {
  const { session } = useAuth();
  const houseId = session?.current_house_id ?? "";
  const { data, error, loading, reload } = useApi(() => apiRequest<Bill[]>("/api/billing/bills"), houseId);
  return <><header className="page-heading"><div><span className="eyebrow">BILLING</span><h1>账单费用</h1><p>仅展示当前房屋的真实账单来源与更新时间。</p></div><button className="button ghost"><CircleHelp size={17} />发起财务咨询</button></header><section className="content-panel"><div className="panel-heading"><h2>当前房屋账单</h2><span className="safe-note">AI 不会修改金额或承诺减免</span></div>{loading ? <Loading /> : error ? <ErrorState error={error} retry={() => void reload()} /> : !data?.length ? <Empty title="当前房屋暂无账单" /> : <div className="bill-grid">{data.map((bill) => <article className="bill-card" key={bill.bill_id}><div><span className="entity-icon"><ReceiptText /></span><span className={`status ${bill.status.toLowerCase()}`}>{bill.status}</span></div><small>{bill.fee_type ?? "综合费用"} · {bill.bill_period}</small><strong>¥ {bill.total_amount}</strong><p>账单号 {bill.bill_id}</p><footer>规则版本：{bill.rule_version ?? "未知"}<br />数据时间：{bill.source_time ? new Date(bill.source_time).toLocaleString("zh-CN") : "未提供"} · 版本 {bill.version}</footer></article>)}</div>}</section></>;
}
