import { AlertTriangle, Building2, LockKeyhole, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { hasCapability } from "../auth/capabilities";
import { describeLoginError } from "../auth/errors";
import { useSession } from "../auth/useSession";
import { Badge, Button, Card, Field, InlineAlert, Input } from "../shared/ui";
import styles from "../styles/app.module.css";

function safeReturnPath(state: unknown): string {
  if (typeof state !== "object" || state === null) return "/";
  const from = (state as { from?: unknown }).from;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") && from !== "/login" ? from : "/";
}

export function RealLoginPage() {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await signIn({ username, password });
      navigate(safeReturnPath(location.state), { replace: true });
    } catch (caught) {
      setError(describeLoginError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return <div className={styles.login}>
    <section className={styles.brandPanel}>
      <Badge>FRONTEND V2</Badge>
      <h1>社区服务，也可以自然、温暖而高效。</h1>
      <p>真实身份与房屋上下文将贯穿后续服务；业务数据会在各垂直流完成迁移后逐步开放。</p>
      <div className={styles.quickActions}>
        <Badge><ShieldCheck size={14} />真实认证</Badge>
        <Badge><Building2 size={14} />房屋作用域隔离</Badge>
      </div>
    </section>
    <main className={styles.loginPanel}>
      <form className={styles.loginForm} onSubmit={(event) => void submit(event)}>
        <span className={styles.eyebrow}>欢迎回来</span>
        <h2>登录社区工作台</h2>
        <p>请使用物业社区系统账号。当前不提供“记住我”或自动续期。</p>
        <Field label="账号"><Input aria-label="账号" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></Field>
        <Field label="密码"><Input aria-label="密码" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></Field>
        {error ? <InlineAlert>{error}</InlineAlert> : null}
        <div className={styles.loginActions}><Button tone="primary" type="submit" disabled={submitting}><LockKeyhole size={17} />{submitting ? "正在登录…" : "登录"}</Button></div>
        <div className={styles.statusNotice}><AlertTriangle size={15} /> 登录状态仅保存在当前浏览器会话中；关闭会话后需重新登录。</div>
      </form>
    </main>
  </div>;
}

export function RealHomePage() {
  const { session, sessionNotice } = useSession();
  if (session.status !== "authenticated") return null;
  const operations = hasCapability(session.actor.roles, "operations");
  const resident = hasCapability(session.actor.roles, "resident-experience");
  return <div className={styles.page}>
    <header className={styles.pageHeader}><div>
      <span className={styles.eyebrow}>{operations ? "OPERATIONS HOME" : "COMMUNITY HOME"}</span>
      <h1>{operations ? `欢迎回来，${session.actor.displayName}` : `你好，${session.actor.displayName}`}</h1>
      <p>{session.actor.communityName} · 身份与房屋作用域已由服务端建立。业务垂直流尚未迁移到 Frontend V2。</p>
    </div></header>
    {sessionNotice ? <InlineAlert>{sessionNotice}</InlineAlert> : null}
    <HouseState />
    {!operations && !resident ? <InlineAlert>当前角色尚未映射到已知产品能力。请联系管理员确认权限。</InlineAlert> : null}
    <MigrationPlaceholder title={operations ? "运营工作台尚未迁移" : "居民服务尚未迁移"} />
  </div>;
}

function HouseState() {
  const { session } = useSession();
  if (session.status !== "authenticated") return null;
  if (session.houses.length === 0) return <InlineAlert>当前账号没有绑定房屋。你仍可使用基础账户能力，房屋作用域页面暂不可用。</InlineAlert>;
  if (!session.currentHouseId) return <InlineAlert>请选择当前房屋后再进入需要房屋作用域的服务。</InlineAlert>;
  const current = session.houses.find((house) => house.id === session.currentHouseId);
  return <Card>
    <span className={styles.eyebrow}>当前房屋作用域</span>
    <h2>{current?.label ?? `房屋 · ${session.currentHouseId.slice(0, 8)}`}</h2>
    <p>{current?.resolved ? "展示信息来自真实房屋选择响应。" : "展示信息尚未解析，当前使用中性房屋标识。"}</p>
  </Card>;
}

export function MigrationPlaceholder({ title = "业务页面尚未迁移" }: { title?: string }) {
  return <div className={styles.notFound}><Building2 size={48} /><h2>{title}</h2><p>这里不会展示 Demo 工单、账单、公告、事件或 Agent 结果。后续将按独立迁移边界接入真实数据。</p></div>;
}

export function BootPage() {
  return <div className={styles.notFound} role="status"><Building2 size={48} /><h2>正在恢复安全会话…</h2><p>身份状态确认前不会显示登录页或业务内容。</p></div>;
}
