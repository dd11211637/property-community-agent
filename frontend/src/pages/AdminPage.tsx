import { AlertTriangle, CheckCircle2, CircleDashed, ServerCog } from "lucide-react";
import { apiRequest } from "../api/client";
import { ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

type Readiness = { status: string; components: Record<string, string> };
export function AdminPage() {
  const ready = useApi(() => apiRequest<Readiness>("/ready"));
  return <><header className="page-heading"><div><span className="eyebrow">OPERATIONS</span><h1>管理工作台</h1><p>聚合待办、失败消息、高风险事件和接口健康状态。</p></div></header><section className="metric-grid"><article><span>待处理事项</span><strong>—</strong><CircleDashed /></article><article><span>失败消息</span><strong>—</strong><AlertTriangle /></article><article><span>高风险事件</span><strong>—</strong><AlertTriangle /></article></section><section className="content-panel"><div className="panel-heading"><h2>接口就绪状态</h2><ServerCog /></div>{ready.loading ? <Loading /> : ready.error ? <ErrorState error={ready.error} retry={() => void ready.reload()} /> : <div className="health-list">{Object.entries(ready.data?.components ?? {}).map(([name, status]) => <div key={name}><CheckCircle2 /><b>{name}</b><span>{status}</span></div>)}</div>}</section></>;
}
