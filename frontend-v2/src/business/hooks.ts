import {
  useMutation,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { useCallback, useMemo, useRef } from "react";
import { ApiError } from "../api/client";
import { useApiClient } from "../api/useApiClient";
import { useSession } from "../auth/useSession";
import { scopeQueryKey, type ResourceIdentity } from "../query/keys";
import { BusinessClient } from "./client";

export type ScopeMode = "house" | "community" | "actor";

export function useBusinessClient(): BusinessClient {
  const api = useApiClient();
  return useMemo(() => new BusinessClient(api), [api]);
}

export function useBusinessKey(
  mode: ScopeMode,
  resource: string,
  identity: ResourceIdentity = {},
): QueryKey {
  const { session } = useSession();
  if (session.status !== "authenticated")
    return ["scope", "anonymous", resource];
  return scopeQueryKey(
    {
      actorId: session.actor.id,
      communityId: session.actor.communityId,
      houseId: session.currentHouseId,
      mode,
    },
    resource,
    identity,
  );
}

export function newIdempotencyKey(): string {
  return `web_v2_${crypto.randomUUID()}`;
}

export function useTransactionalMutation<TInput, TOutput>({
  execute,
  invalidate,
  idempotent = true,
}: {
  execute(input: TInput, idempotencyKey?: string): Promise<TOutput>;
  invalidate: readonly QueryKey[];
  idempotent?: boolean;
}) {
  const queryClient = useQueryClient();
  const intent = useRef<{ input: TInput; key?: string } | null>(null);
  const run = useCallback(
    async (input: TInput) => {
      const key = idempotent ? newIdempotencyKey() : undefined;
      intent.current = { input, key };
      return execute(input, key);
    },
    [execute, idempotent],
  );
  const mutation = useMutation({
    mutationFn: run,
    retry: false,
    onSuccess: async () => {
      intent.current = null;
      await Promise.all(
        invalidate.map((queryKey) =>
          queryClient.invalidateQueries({ queryKey }),
        ),
      );
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.kind === "conflict") {
        intent.current = null;
        await Promise.all(
          invalidate.map((queryKey) =>
            queryClient.invalidateQueries({ queryKey }),
          ),
        );
      }
    },
  });
  const retrySameIntent = useCallback(async () => {
    if (!intent.current) return undefined;
    const { input, key } = intent.current;
    return execute(input, key);
  }, [execute]);
  return {
    ...mutation,
    retrySameIntent,
    resetIntent: () => {
      intent.current = null;
      mutation.reset();
    },
  };
}

export function describeBusinessError(error: unknown): string {
  if (error instanceof ApiError) {
    const request = error.requestId ? `（请求 ${error.requestId}）` : "";
    if (error.kind === "conflict")
      return `资源已被其他操作更新。已重新加载最新数据，请核对后重新提交。${request}`;
    if (error.kind === "forbidden")
      return `当前账号无权执行此操作，登录状态仍然有效。${request}`;
    if (error.status === 404) return `资源不存在或已不可访问。${request}`;
    if (error.kind === "validation") {
      const fields = Array.isArray(error.details?.errors)
        ? error.details.errors
            .map((item) =>
              typeof item === "object" && item && "msg" in item
                ? String((item as { msg: unknown }).msg)
                : "字段无效",
            )
            .join("；")
        : "";
      return `提交内容未通过服务端校验，请检查表单。${fields ? ` ${fields}` : ""}${request}`;
    }
    if (error.kind === "rate-limited")
      return `操作过于频繁，请稍后再试。${request}`;
    if (error.kind === "unavailable")
      return `业务服务暂时不可用，请稍后重试。${request}`;
    if (error.kind === "network") return "无法连接业务服务，请检查网络。";
    if (error.kind === "timeout")
      return "业务服务响应超时；可重试同一操作，系统会复用幂等标识。";
    return `${error.message}${request}`;
  }
  if (error instanceof Error) return error.message;
  return "发生未知错误。";
}
