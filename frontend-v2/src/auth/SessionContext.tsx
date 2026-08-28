import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { isScopeQuery } from "../query/keys";
import type { AuthenticationPort, Credentials, SessionStore } from "./session";
import { SessionContext, type SessionContextValue } from "./sessionContextDefinition";

export function SessionProvider({ store, auth, children }: {
  store: SessionStore;
  auth: AuthenticationPort;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const session = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const [transitioning, setTransitioning] = useState(false);

  const signIn = useCallback(async (credentials: Credentials) => {
    const next = await auth.signIn(credentials);
    const previous = store.getSnapshot();
    if (previous.status === "authenticated" && next.status === "authenticated" && previous.actor.id !== next.actor.id) {
      queryClient.clear();
    }
    store.setState(next);
  }, [auth, queryClient, store]);

  const signOut = useCallback(() => {
    queryClient.clear();
    store.clear();
  }, [queryClient, store]);

  const selectHouse = useCallback(async (houseId: string) => {
    const current = store.getSnapshot();
    if (current.status !== "authenticated" || current.currentHouseId === houseId) return;
    setTransitioning(true);
    await queryClient.cancelQueries({
      predicate: (query) => isScopeQuery(query.queryKey, current.actor.id, current.currentHouseId),
    });
    store.setState({ ...current, currentHouseId: houseId });
    setTransitioning(false);
  }, [queryClient, store]);

  const value = useMemo<SessionContextValue>(() => ({ session, transitioning, signIn, signOut, selectHouse }), [session, transitioning, signIn, signOut, selectHouse]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
