import { ArrowRight, Building2, CheckCircle2, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EnvironmentBadge } from "../components/EnvironmentBadge";

export function LoginPage() {
  const { login, session } = useAuth();
  const navigate = useNavigate();
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => { if (session) navigate("/", { replace: true }); }, [navigate, session]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError("");
    try { await login(account.trim(), password); }
    catch (reason) {
      setError(reason instanceof ApiError && reason.status === 503
        ? "登录服务尚未装配，请联系后端负责人完成联调。"
        : reason instanceof Error ? reason.message : "登录失败");
    } finally { setPending(false); }
  };
  return (
    <main className="login-page">
      <EnvironmentBadge />
      <section className="login-story">
        <div className="login-brand"><span className="brand-mark"><Building2 /></span><b>栖邻</b></div>
        <div>
          <span className="eyebrow light">PROPERTY COMMUNITY</span>
          <h1>让每一次社区服务<br />都有回应，有进展。</h1>
          <p>报修、账单、公告与巡检，在一个可信、可追踪的服务入口完成。</p>
        </div>
        <ul>
          <li><CheckCircle2 /> 真实业务状态，全程可追踪</li>
          <li><ShieldCheck /> 身份与房屋范围由服务端校验</li>
        </ul>
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <span className="eyebrow">社区服务入口</span>
          <h2>欢迎回来</h2>
          <p>使用项目提供的演示账号登录</p>
          <label>账号<input required autoComplete="username" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="请输入演示账号" /></label>
          <label>密码<input required type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="请输入密码" /></label>
          {error && <div className="inline-error" role="alert">{error}</div>}
          <button className="button primary wide" disabled={pending}>{pending ? "正在验证…" : <>登录并选择房屋 <ArrowRight size={17} /></>}</button>
          <small className="form-note">角色、小区和房屋权限均由后端签发，页面不会接受手工覆盖。</small>
        </form>
      </section>
    </main>
  );
}
