import { useMemo } from "react";
import { useApiClient } from "../api/useApiClient";
import { useSession } from "../auth/useSession";
import { AgentService } from "./service";

export function useAgentService(): AgentService {
  const api = useApiClient();
  return useMemo(() => new AgentService(api), [api]);
}
export function useAgentKey(
  resource: string,
  identity: Readonly<Record<string, string | number | null>> = {},
) {
  const { session } = useSession();
  if (session.status !== "authenticated") return ["agent", "anonymous", resource] as const;
  return [
    "agent",
    { actorId: session.actor.id, communityId: session.actor.communityId },
    resource,
    identity,
  ] as const;
}
