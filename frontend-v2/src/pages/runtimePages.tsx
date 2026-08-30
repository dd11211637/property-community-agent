import {
  AlertTriangle,
  Building2,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { describeLoginError } from "../auth/errors";
import { useSession } from "../auth/useSession";
import { Badge, Button, Field, InlineAlert, Input } from "../shared/ui";
import styles from "../styles/app.module.css";

function safeReturnPath(state: unknown): string {
  if (typeof state !== "object" || state === null) return "/";
  const from = (state as { from?: unknown }).from;
  return typeof from === "string" &&
    from.startsWith("/") &&
    !from.startsWith("//") &&
    from !== "/login"
    ? from
    : "/";
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

  return (
    <div className={styles.login}>
      <section className={styles.brandPanel}>
        <Badge>FRONTEND V2</Badge>
        <h1>社区服务，也可以自然、温暖而高效。</h1>
        <p>
          真实身份、房屋上下文、业务服务、Agent 与长期记忆已安全接入。
        </p>
        <div className={styles.quickActions}>
          <Badge>
            <ShieldCheck size={14} />
            真实认证
          </Badge>
          <Badge>
            <Building2 size={14} />
            房屋作用域隔离
          </Badge>
        </div>
      </section>
      <main className={styles.loginPanel}>
        <form
          className={styles.loginForm}
          onSubmit={(event) => void submit(event)}
        >
          <span className={styles.eyebrow}>欢迎回来</span>
          <h2>登录社区工作台</h2>
          <p>请使用物业社区系统账号。当前不提供“记住我”或自动续期。</p>
          <Field label="账号">
            <Input
              aria-label="账号"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </Field>
          <Field label="密码">
            <Input
              aria-label="密码"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          {error ? <InlineAlert>{error}</InlineAlert> : null}
          <div className={styles.loginActions}>
            <Button tone="primary" type="submit" disabled={submitting}>
              <LockKeyhole size={17} />
              {submitting ? "正在登录…" : "登录"}
            </Button>
          </div>
          <div className={styles.statusNotice}>
            <AlertTriangle size={15} />{" "}
            登录状态仅保存在当前浏览器会话中；关闭会话后需重新登录。
          </div>
        </form>
      </main>
    </div>
  );
}

export function BootPage() {
  return (
    <div className={styles.notFound} role="status">
      <Building2 size={48} />
      <h2>正在恢复安全会话…</h2>
      <p>身份状态确认前不会显示登录页或业务内容。</p>
    </div>
  );
}
