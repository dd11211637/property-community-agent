import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, BellRing, Bot, Building2, ClipboardCheck, FileText, LifeBuoy, LockKeyhole, MessageCircle, Plus, Search, ShieldCheck, Sparkles, WalletCards, Wrench } from "lucide-react";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { AgentComposer, AgentWorkspace, ConfirmationCard, MessageBubble, SuggestedAction } from "../agent/components";
import { AnnouncementCard, BillCard, HouseCard, InspectionTaskCard, ResidentCard, SecurityEventCard, WorkOrderCard } from "../domain/cards";
import { useShowcaseModels } from "../models/useShowcaseModels";
import { formatCurrency } from "../presentation/format";
import { Dialog } from "../shared/overlays";
import { Badge, Button, Card, Field, InlineAlert, Input, Tabs, Textarea } from "../shared/ui";
import styles from "../styles/app.module.css";

function Page({ children }: { children: ReactNode }) { return <div className={styles.page}>{children}</div>; }
function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) { return <header className={styles.pageHeader}><div><span className={styles.eyebrow}>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{action}</header>; }
function Section({ title, meta, children }: { title: string; meta?: string; children: ReactNode }) { return <section className={styles.section}><div className={styles.sectionHeading}><h2>{title}</h2>{meta ? <span>{meta}</span> : null}</div>{children}</section>; }
function MetricCard({ label, value, note }: { label: string; value: string; note: string }) { return <Card className={styles.metric}><span>{label}</span><strong>{value}</strong><span>{note}</span></Card>; }

export function HomePage() {
  const { session } = useSession();
  if (session.status !== "authenticated") return null;
  return hasCapability(session.actor.roles, "operations") ? <OperationsHome /> : <ResidentHome />;
}

function ResidentHome() {
  const models = useShowcaseModels();
  return <Page><section className={styles.hero}><div className={styles.heroMain}><Badge>下午好 · 桂语社区</Badge><h1>生活里的小事，交给我一起处理。</h1><p>从自然语言开始，也随时可以直接查看报修、账单与社区动态。所有执行动作都会先由你确认。</p><AgentComposer /></div><aside className={styles.priority}><BellRing /><h2>今天需要留意</h2><p>电梯维保将在 14:00 开始，预计持续两小时。你的报修工单也有了新进展。</p><Button tone="secondary">查看今日动态 <ArrowRight size={17} /></Button></aside></section><div className={styles.quickActions}><Button tone="secondary"><Wrench size={17} />发起报修</Button><Button tone="secondary"><WalletCards size={17} />查询账单</Button><Button tone="secondary"><MessageCircle size={17} />联系物业</Button><Button tone="secondary"><LifeBuoy size={17} />紧急帮助</Button></div><Section title="当前事项" meta="与当前房屋相关"><div className={styles.grid2}><WorkOrderCard value={models.workOrders[0]} /><BillCard value={models.bills[0]} /></div></Section><Section title="社区动态" meta="最近更新"><div className={styles.grid3}>{models.announcements.slice(0, 3).map((item) => <AnnouncementCard key={item.id} value={item} compact />)}</div></Section><Section title="Agent 建议"><div className={styles.grid2}><SuggestedAction label="补充报修现场照片" description="可以帮助维修人员提前准备工具" /><ConfirmationCard title="是否订阅停水提醒？" description="仅在当前房屋受到影响时通知你。" confirmLabel="确认订阅" /></div></Section></Page>;
}

function OperationsHome() {
  const models = useShowcaseModels();
  return <Page><PageHeader eyebrow="OPERATIONS WORKSPACE" title="今天，从最重要的事项开始" description="会话、业务对象与住户上下文在同一个工作面完成闭环。" action={<Button tone="primary"><Sparkles size={17} />询问 Agent</Button>} /><div className={styles.operationsHome}><aside className={styles.rail}><h2>最近会话</h2>{models.conversations.map((item) => <button className={styles.conversation} key={item.id}><strong>{item.name}</strong><span>{item.preview}</span><small>{item.time}</small></button>)}</aside><section className={styles.workspacePanel}><div className={styles.sectionHeading}><h2>AI 工作区</h2><Badge tone="success">上下文已连接</Badge></div><AgentWorkspace results={models.agentResults.slice(0, 4)} /></section><aside className={styles.contextPanel}><h2>当前上下文</h2><ResidentCard value={models.residents[0]} variant="context" /><HouseCard value={models.houses[0]} variant="context" /><WorkOrderCard value={models.workOrders[0]} variant="context" /></aside></div></Page>;
}

export function RepairsPage() {
  const models = useShowcaseModels();
  return <Page><PageHeader eyebrow="REPAIRS" title="报修与工单" description="用清晰的状态、位置和下一步行动组织报修，不让重要信息淹没在表格中。" action={<Dialog trigger={<Button tone="primary"><Plus size={17} />新建报修</Button>} title="描述需要处理的问题" description="这是视觉骨架，不会提交真实业务数据。"><div className={styles.section}><Field label="具体位置"><Input placeholder="例如：客厅窗边" /></Field><Field label="问题描述"><Textarea placeholder="发生了什么？" /></Field><InlineAlert>正式迁移后，提交前会展示确认信息并携带幂等键。</InlineAlert></div></Dialog>} /><div className={styles.metrics}><MetricCard label="处理中" value="8" note="2 项今日有更新" /><MetricCard label="等待确认" value="3" note="需要住户补充信息" /><MetricCard label="今日完成" value="12" note="平均用时 3.6 小时" /></div><div className={styles.quickActions}><Button tone="secondary"><Search size={16} />全部状态</Button><Button tone="ghost">高优先级</Button><Button tone="ghost">待派单</Button></div><Section title="工单列表" meta={`${models.workOrders.length} 项`}><div className={styles.grid3}>{models.workOrders.map((item) => <WorkOrderCard key={item.id} value={item} />)}</div></Section></Page>;
}

export function BillingPage() {
  const models = useShowcaseModels();
  const total = models.bills.filter((bill) => bill.status !== "PAID").reduce((sum, bill) => sum + bill.total, 0);
  return <Page><PageHeader eyebrow="BILLING" title="账单与费用" description="先看清当前余额，再按需展开明细、历史与咨询记录。" action={<Button tone="secondary"><MessageCircle size={17} />费用咨询</Button>} /><Card><span className={styles.eyebrow}>当前待缴</span><p style={{ fontSize: "2.8rem", margin: "8px 0", fontWeight: 760 }}>{formatCurrency(total)}</p><span>最近缴费截止日：2026-09-15</span></Card><Section title="账单记录" meta="近三个月"><div className={styles.grid3}>{models.bills.map((item) => <BillCard key={item.id} value={item} />)}</div></Section></Page>;
}

export function CommunityPage() {
  const models = useShowcaseModels();
  return <Page><PageHeader eyebrow="COMMUNITY" title="社区动态" description="重要通知优先，普通动态保持轻量；面向工作人员时仍保留发布状态与受众信息。" action={<Button tone="secondary"><FileText size={17} />订阅设置</Button>} /><Card><div className={styles.sectionHeading}><div><Badge tone="warning">重要通知</Badge><h2>本周电梯维保安排</h2></div><BellRing /></div><p>1 号楼客梯将在周六 14:00–16:00 进行例行维保，请提前安排出行。</p></Card><Section title="全部动态"><div className={styles.grid3}>{models.announcements.map((item) => <AnnouncementCard key={item.id} value={item} />)}</div></Section></Page>;
}

export function OperationsPage() {
  const models = useShowcaseModels();
  const [tab, setTab] = useState("inspection");
  return <Page><PageHeader eyebrow="OPERATIONS" title="运营态势" description="巡检、安防事件与今日任务共享同一套风险与状态语言。" action={<Button tone="primary"><ClipboardCheck size={17} />创建任务</Button>} /><div className={styles.metrics}><MetricCard label="巡检任务" value="24" note="18 项已完成" /><MetricCard label="高风险事件" value="2" note="均已进入人工处置" /><MetricCard label="今日完成率" value="75%" note="较昨日提升 8%" /></div><Tabs active={tab} onChange={setTab} items={[{ id: "inspection", label: "巡检" }, { id: "security", label: "安防事件" }, { id: "today", label: "今日任务" }]} />{tab === "inspection" ? <div className={styles.grid3}>{models.inspections.map((item) => <InspectionTaskCard key={item.id} value={item} />)}</div> : tab === "security" ? <div className={styles.grid3}>{models.securityEvents.map((item) => <SecurityEventCard key={item.id} value={item} />)}</div> : <div className={styles.grid2}><SuggestedAction label="完成 3 号楼消防通道复查" description="17:00 前提交现场结果" /><SuggestedAction label="回访昨夜噪声事件" description="确认住户是否仍需协助" /></div>}</Page>;
}

export function MessagesPage() {
  const models = useShowcaseModels();
  const { session } = useSession();
  const operations = session.status === "authenticated" && hasCapability(session.actor.roles, "operations");
  return <Page><PageHeader eyebrow="UNIFIED INBOX" title="消息与会话" description="对话不脱离业务上下文：当前住户、房屋和关联工单始终可见。" /><div className={styles.messageLayout}><aside className={styles.rail}><h2>会话</h2>{models.conversations.map((item) => <button className={styles.conversation} key={item.id}><strong>{item.name}</strong><span>{item.preview}</span><small>{item.time}</small></button>)}</aside><Card className={styles.thread}>{models.messages.map((item) => <MessageBubble key={item.id} sender={item.sender === "user" ? "user" : "assistant"}>{item.body}</MessageBubble>)}<AgentComposer placeholder="输入消息…" /></Card>{operations ? <aside className={styles.contextPanel}><h2>关联上下文</h2><ResidentCard value={models.residents[0]} variant="context" /><WorkOrderCard value={models.workOrders[0]} variant="context" /></aside> : <aside className={styles.contextPanel}><h2>服务进度</h2><WorkOrderCard value={models.workOrders[0]} variant="context" /></aside>}</div></Page>;
}

export function AdminPage() {
  const models = useShowcaseModels();
  return <Page><PageHeader eyebrow="ADMIN" title="管理与服务状态" description="只承载系统、身份、房屋和服务健康，不成为无边界的功能收纳箱。" action={<Badge tone="warning">DEMO ENVIRONMENT</Badge>} /><div className={styles.metrics}><MetricCard label="活跃用户" value="1,284" note="近 30 日 +6.2%" /><MetricCard label="服务健康" value="6 / 6" note="核心依赖正常" /><MetricCard label="待处理异常" value="3" note="均已分派责任人" /></div><Section title="身份与房屋"><div className={styles.grid2}><ResidentCard value={models.residents[0]} /><HouseCard value={models.houses[0]} /></div></Section><Section title="服务状态"><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>服务</th><th>状态</th><th>延迟</th><th>环境</th></tr></thead><tbody><tr><td>Backend API</td><td><Badge tone="success">正常</Badge></td><td>42 ms</td><td>Skeleton</td></tr><tr><td>Agent Runtime</td><td><Badge tone="warning">未接入</Badge></td><td>—</td><td>Skeleton</td></tr><tr><td>PostgreSQL</td><td><Badge tone="success">正常</Badge></td><td>18 ms</td><td>Reference</td></tr></tbody></table></div></Section></Page>;
}

export function LoginPage() {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const [account, setAccount] = useState("resident");
  const [password, setPassword] = useState("preview");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try { await signIn({ account, password }); navigate("/"); } catch (caught) { setError(caught instanceof Error ? caught.message : "无法进入预览"); }
  }
  return <div className={styles.login}><section className={styles.brandPanel}><Badge>FRONTEND V2 SKELETON</Badge><h1>社区服务，也可以自然、温暖而高效。</h1><p>AI、业务对象和人工确认不是三个孤岛。它们将在同一个工作空间里共同支持居民与运营人员。</p><div className={styles.quickActions}><Badge><Bot size={14} />Agent-native</Badge><Badge><ShieldCheck size={14} />Scope-safe</Badge><Badge><Building2 size={14} />Multi-house ready</Badge></div></section><main className={styles.loginPanel}><form className={styles.loginForm} onSubmit={(event) => void submit(event)}><span className={styles.eyebrow}>欢迎回来</span><h2>进入产品预览</h2><p>输入 resident 查看居民体验，输入 manager 查看运营工作台。本阶段不接入真实认证。</p><Field label="预览身份"><Input aria-label="预览身份" value={account} onChange={(event) => setAccount(event.target.value)} autoComplete="username" /></Field><Field label="预览口令"><Input aria-label="预览口令" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></Field>{error ? <div role="alert">{error}</div> : null}<div className={styles.loginActions}><Button tone="primary" type="submit"><LockKeyhole size={17} />进入工作空间</Button><Button tone="ghost" type="button" onClick={() => { setAccount("manager"); setPassword("preview"); }}>切换为运营预览</Button></div><div className={styles.statusNotice}><AlertTriangle size={15} /> Skeleton 使用内存会话；刷新页面后不会保留身份或令牌。</div></form></main></div>;
}

export function ForbiddenPage() { return <div className={styles.notFound}><LockKeyhole size={48} /><h2>当前身份无权访问</h2><p>前端守卫只负责用户体验，真实授权仍由后端决定。</p><Button tone="primary" onClick={() => history.back()}>返回上一页</Button></div>; }
export function NotFoundPage() { const navigate = useNavigate(); return <div className={styles.notFound}><h1>404</h1><h2>这里还没有社区服务</h2><p>地址可能已变化，或该页面尚未加入工作空间。</p><Button tone="primary" onClick={() => navigate("/")}>返回首页</Button></div>; }
