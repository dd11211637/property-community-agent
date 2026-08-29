import { createContext, useContext } from "react";

export type AgentRuntimeValue = {
  createController(): AbortController;
  releaseController(controller: AbortController): void;
  abortAll(): void;
};

export const AgentRuntimeContext = createContext<AgentRuntimeValue | null>(null);

export function isAgentQueryKey(key: readonly unknown[]): boolean {
  return key[0] === "agent";
}
export function useAgentRuntime(): AgentRuntimeValue {
  const value = useContext(AgentRuntimeContext);
  if (!value) throw new Error("useAgentRuntime must be used inside AgentRuntimeProvider");
  return value;
}
