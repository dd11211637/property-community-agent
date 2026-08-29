import { useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { useSession } from "../auth/useSession";
import { AgentRuntimeContext, isAgentQueryKey, type AgentRuntimeValue } from "./runtimeDefinition";

export function AgentRuntimeProvider({ children }: { children: ReactNode }) {
  const { session, transitioning } = useSession();
  const queryClient = useQueryClient();
  const controllers = useRef(new Set<AbortController>());
  const identity =
    session.status === "authenticated"
      ? `${session.actor.id}:${session.currentHouseId ?? "none"}`
      : "anonymous";
  const previousIdentity = useRef(identity);

  const abortAll = useCallback(() => {
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
  }, []);

  useEffect(() => {
    if (transitioning || previousIdentity.current !== identity) abortAll();
    if (previousIdentity.current.split(":")[0] !== identity.split(":")[0]) {
      void queryClient.cancelQueries({ predicate: (query) => isAgentQueryKey(query.queryKey) });
      queryClient.removeQueries({ predicate: (query) => isAgentQueryKey(query.queryKey) });
    }
    previousIdentity.current = identity;
  }, [abortAll, identity, queryClient, transitioning]);

  useEffect(() => abortAll, [abortAll]);

  const value = useMemo<AgentRuntimeValue>(
    () => ({
      createController() {
        const controller = new AbortController();
        controllers.current.add(controller);
        return controller;
      },
      releaseController(controller) {
        controllers.current.delete(controller);
      },
      abortAll,
    }),
    [abortAll],
  );
  return <AgentRuntimeContext.Provider value={value}>{children}</AgentRuntimeContext.Provider>;
}
