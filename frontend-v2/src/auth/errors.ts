import { ApiError } from "../api/client";

export function describeLoginError(error: unknown): string {
  if (!(error instanceof ApiError)) return "登录响应无法验证，请稍后重试。";
  const messages = {
    unauthenticated: "账号或密码错误，请重新输入。",
    "rate-limited": "尝试次数过多，账号已暂时锁定，请稍后再试。",
    network: "暂时无法连接服务，请检查网络后重试。",
    timeout: "登录请求超时，请稍后重试。",
    unavailable: "认证服务暂时不可用，请稍后重试。",
    "invalid-response": "登录响应无法验证，请稍后重试。",
  } satisfies Partial<Record<ApiError["kind"], string>>;
  return messages[error.kind as keyof typeof messages] ?? "登录未成功，请稍后重试。";
}

export function describeHouseError(error: unknown): string {
  if (!(error instanceof ApiError)) return "房屋信息暂时无法验证，原房屋未改变。";
  if (error.kind === "forbidden") return "当前账号无权使用该房屋，原房屋未改变。";
  if (error.kind === "unauthenticated") return "认证已失效，请重新登录。";
  if (["network", "timeout", "unavailable"].includes(error.kind)) return "房屋信息暂时无法验证，原房屋未改变。";
  return "无法切换到该房屋，原房屋未改变。";
}
