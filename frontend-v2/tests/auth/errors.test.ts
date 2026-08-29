import { describe, expect, it } from "vitest";
import { ApiError, type ApiErrorKind } from "../../src/api/client";
import { describeLoginError } from "../../src/auth/errors";

describe("login error presentation", () => {
  it.each([
    ["unauthenticated", "账号或密码错误"],
    ["rate-limited", "暂时锁定"],
    ["network", "检查网络"],
    ["timeout", "请求超时"],
    ["unavailable", "认证服务暂时不可用"],
    ["invalid-response", "响应无法验证"],
  ] as const)("gives %s a distinct recovery message", (kind, message) => {
    const error = new ApiError(kind as ApiErrorKind, 0, "TEST", "internal backend detail");
    expect(describeLoginError(error)).toContain(message);
    expect(describeLoginError(error)).not.toContain("internal backend detail");
  });
});
