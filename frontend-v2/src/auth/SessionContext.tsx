import { useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { isScopeQuery } from "../query/keys";
import { describeHouseError } from "./errors";
import {
  resolvedHouse,
  type AuthenticationPort,
  type AuthenticatedSession,
  type Credentials,
  type SessionStore,
} from "./session";
import {
  SessionContext,
  type SessionContextValue,
} from "./sessionContextDefinition";

function withResolvedHouse(
  session: AuthenticatedSession,
  houseId: string,
  building: string,
  unit: string,
  roomNo: string,
): AuthenticatedSession {
  const house = resolvedHouse(houseId, building, unit, roomNo);
  return {
    ...session,
    houses: session.houses.map((item) => (item.id === houseId ? house : item)),
    currentHouseId: houseId,
  };
}

export function SessionProvider({
  store,
  auth,
  children,
}: {
  store: SessionStore;
  auth: AuthenticationPort;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const session = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
  const [transitioning, setTransitioning] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const [sessionNotice, setSessionNotice] = useState("");
  const transitionSequence = useRef(0);

  useEffect(() => {
    store.restore();
  }, [store]);

  const resolveInitialHouse = useCallback(
    async (next: AuthenticatedSession) => {
      if (!next.currentHouseId || next.houses.length !== 1) return;
      try {
        const result = await auth.selectHouse(next.currentHouseId);
        const current = store.getSnapshot();
        if (
          current.status === "authenticated" &&
          current.actor.id === next.actor.id
        ) {
          store.setState(
            withResolvedHouse(
              current,
              result.houseId,
              result.building,
              result.unit,
              result.roomNo,
            ),
          );
        }
      } catch (error) {
        if (store.getSnapshot().status === "authenticated")
          setSessionNotice(describeHouseError(error));
      }
    },
    [auth, store],
  );

  const signIn = useCallback(
    async (credentials: Credentials) => {
      const next = await auth.signIn(credentials);
      const previous = store.getSnapshot();
      if (
        previous.status === "authenticated" &&
        previous.actor.id !== next.actor.id
      ) {
        await queryClient.cancelQueries();
        queryClient.clear();
      }
      setSelectionError("");
      setSessionNotice("");
      store.setState(next);
      await resolveInitialHouse(next);
    },
    [auth, queryClient, resolveInitialHouse, store],
  );

  const signOut = useCallback(async () => {
    transitionSequence.current += 1;
    await queryClient.cancelQueries();
    queryClient.clear();
    store.clear();
    setTransitioning(false);
    setSelectionError("");
    setSessionNotice("");
  }, [queryClient, store]);

  const selectHouse = useCallback(
    async (houseId: string) => {
      const current = store.getSnapshot();
      if (
        current.status !== "authenticated" ||
        current.currentHouseId === houseId ||
        !current.houses.some((house) => house.id === houseId)
      )
        return;
      const sequence = ++transitionSequence.current;
      setTransitioning(true);
      setSelectionError("");
      queryClient.getMutationCache().clear();
      await queryClient.cancelQueries({
        predicate: (query) =>
          isScopeQuery(
            query.queryKey,
            current.actor.id,
            current.currentHouseId,
          ),
      });
      try {
        const selected = await auth.selectHouse(houseId);
        if (sequence !== transitionSequence.current) return;
        const latest = store.getSnapshot();
        if (
          latest.status !== "authenticated" ||
          latest.actor.id !== current.actor.id
        )
          return;
        store.setState(
          withResolvedHouse(
            latest,
            selected.houseId,
            selected.building,
            selected.unit,
            selected.roomNo,
          ),
        );
        queryClient.removeQueries({
          predicate: (query) =>
            isScopeQuery(
              query.queryKey,
              current.actor.id,
              current.currentHouseId,
            ),
        });
      } catch (error) {
        if (
          sequence === transitionSequence.current &&
          store.getSnapshot().status === "authenticated"
        ) {
          setSelectionError(describeHouseError(error));
          await queryClient.invalidateQueries({
            predicate: (query) =>
              isScopeQuery(
                query.queryKey,
                current.actor.id,
                current.currentHouseId,
              ),
          });
        }
      } finally {
        if (sequence === transitionSequence.current) setTransitioning(false);
      }
    },
    [auth, queryClient, store],
  );

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      transitioning,
      selectionError,
      sessionNotice,
      signIn,
      signOut,
      selectHouse,
    }),
    [
      session,
      transitioning,
      selectionError,
      sessionNotice,
      signIn,
      signOut,
      selectHouse,
    ],
  );
  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}
