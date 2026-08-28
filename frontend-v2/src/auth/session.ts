export type ActorRole = string;

export type Actor = {
  id: string;
  displayName: string;
  roles: ActorRole[];
  communityName: string;
};

export type House = {
  id: string;
  label: string;
  address?: string;
};

export type SessionState =
  | { status: "unauthenticated" }
  | {
      status: "authenticated";
      actor: Actor;
      houses: House[];
      currentHouseId: string | null;
      accessToken?: string;
    };

export interface SessionStore {
  getSnapshot(): SessionState;
  subscribe(listener: () => void): () => void;
  setState(next: SessionState): void;
  clear(): void;
}

export function createInMemorySessionStore(initial: SessionState = { status: "unauthenticated" }): SessionStore {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setState(next) {
      state = next;
      listeners.forEach((listener) => listener());
    },
    clear() {
      state = { status: "unauthenticated" };
      listeners.forEach((listener) => listener());
    },
  };
}

export type Credentials = { account: string; password: string };

export interface AuthenticationPort {
  signIn(credentials: Credentials): Promise<SessionState>;
}
