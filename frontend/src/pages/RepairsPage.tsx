import { Clock3, Plus, Wrench } from "lucide-react";
import { useState, type FormEvent } from "react";
import { apiRequest, createIdempotencyKey } from "../api/client";
import type { ListResult, WorkOrder } from "../api/contracts";
import { useAuth } from "../auth/AuthContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Empty, ErrorState, Loading } from "../components/AsyncState";
import { useApi } from "../hooks/useApi";

export function RepairsPage() {
  const { session } = useAuth();
  const { data, error, loading, reload } = useApi(() => apiRequest<ListResult<WorkOrder>>("/api/work-orders"));
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState({ category: "OTHER", location: "", description: "", urgency: "NORMAL" });
  const [confirm, setConfirm] = useState(false);
  const [operationKey, setOperationKey] = useState("");
  const stage = (event: FormEvent) => {
    event.preventDefault();
    setOperationKey(createIdempotencyKey("repair-create"));
    setConfirm(true);
  };
  const create = async () => {
    if (!session?.current_house_id) throw new Error("请先选择当前房屋。");
    const parameters = { ...draft, house_id: session.current_house_id, attachment_ids: [] };
    const confirmation = await apiRequest<{ token: string }>("/api/confirmations", {
      method: "POST",
      idempotencyKey: `${operationKey}-confirmation`,
      body: { action: "CREATE_WORK_ORDER", parameters },
    });
    await apiRequest("/api/work-orders", {
      method: "POST",
      idempotencyKey: operationKey,
      body: { ...parameters, confirmation_token: confirmation.token },
    });
    setFormOpen(false); setDraft({ category: "OTHER", location: "", description: "", urgency: "NORMAL" }); await reload();
  };
  return (
    <>
      <header className="page-heading"><div><span className="eyebrow">REPAIR SERVICE</span><h1>报修服务</h1><p>提交问题，查看处理时间线与下一步操作。</p></div><button className="button primary" onClick={() => setFormOpen(true)}><Plus size={17} />新建报修</button></header>
      {formOpen && <form className="form-card" onSubmit={stage}><div className="form-card-title"><Wrench /><div><h2>描述需要处理的问题</h2><p>只填写必要信息，提交前会再次核对。</p></div></div><div className="form-grid"><label>问题类型<select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}><option value="WATER_PLUMBING">水暖管道</option><option value="ELECTRICAL">电气</option><option value="ELEVATOR">电梯</option><option value="OTHER">其他</option></select></label><label>紧急程度<select value={draft.urgency} onChange={(e) => setDraft({ ...draft, urgency: e.target.value })}><option value="NORMAL">普通</option><option value="URGENT">紧急</option></select></label><label className="span-2">具体位置<input required maxLength={128} value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} /></label><label className="span-2">问题描述<textarea required value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label></div><div className="form-actions"><button type="button" className="button ghost" onClick={() => setFormOpen(false)}>取消</button><button className="button primary">核对并提交</button></div></form>}
      <section className="content-panel"><div className="panel-heading"><h2>我的工单</h2><button className="text-button" onClick={() => void reload()}>刷新</button></div>{loading ? <Loading /> : error ? <ErrorState error={error} retry={() => void reload()} /> : !data?.items.length ? <Empty title="还没有报修记录" detail="遇到设施问题时，可从右上角新建报修。" /> : <div className="entity-list">{data.items.map((item) => <article className="entity-card" key={item.id}><div className="entity-icon"><Wrench /></div><div className="entity-main"><div><span className={`status ${item.status.toLowerCase()}`}>{item.status}</span><small>#{item.id.slice(0, 8)}</small></div><h3>{item.location}</h3><p>{item.description}</p><span className="meta"><Clock3 size={14} />版本 {item.version} · {item.urgency}</span></div></article>)}</div>}</section>
      {confirm && <ConfirmDialog title="确认创建报修工单" summary={<dl className="summary-list"><div><dt>房屋</dt><dd>{session?.houses.find((h) => h.id === session.current_house_id)?.label ?? "未选择"}</dd></div><div><dt>位置</dt><dd>{draft.location}</dd></div><div><dt>描述</dt><dd>{draft.description}</dd></div></dl>} onClose={() => setConfirm(false)} onConfirm={create} />}
    </>
  );
}
