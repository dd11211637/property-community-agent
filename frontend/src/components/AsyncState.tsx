import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { ApiError } from "../api/client";

const statusCopy: Record<number, string> = {
  401: "登录已失效，请重新登录。",
  403: "当前身份无权查看或操作此内容。",
  404: "内容不存在，或你无权知道它是否存在。",
  409: "数据已被更新，请刷新后再试。",
  422: "提交内容有误，请检查填写项。",
  503: "此能力尚未装配或服务暂时不可用。",
};

export function Loading({ label = "正在加载" }: { label?: string }) {
  return <div className="state-card skeleton-state" role="status" aria-busy="true" aria-label={label}><div className="skeleton-line wide" /><div className="skeleton-line" /><div className="skeleton-line short" /><span>{label}</span></div>;
}

export function Empty({ title = "暂无数据", detail = "有新内容时会显示在这里。" }) {
  return <div className="state-card"><Inbox /><strong>{title}</strong><p>{detail}</p></div>;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  const message = apiError ? statusCopy[apiError.status] ?? apiError.message : "页面加载失败，请稍后重试。";
  return (
    <div className="state-card error-state" role="alert">
      <AlertTriangle />
      <strong>{message}</strong>
      {apiError?.requestId && <small>请求编号：{apiError.requestId}</small>}
      {retry && <button className="button ghost" onClick={retry}><RefreshCw size={16} />重试</button>}
    </div>
  );
}
