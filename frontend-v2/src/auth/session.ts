export const browserSessionKey = "property_agent_v2_session";
export const browserSessionVersion = 1;

export type ActorRole = string;
export type Actor = { id: string; displayName: string; communityId: string; communityName: string; roles: ActorRole[] };
export type House = {
  id: string; label: string; resolved: boolean; building?: string; unit?: string; roomNo?: string;
};
export type AuthenticatedSession = {
  status: "authenticated"; accessToken: string; actor: Actor; houses: House[]; currentHouseId: string | null;
};
export type SessionState = { status: "restoring" } | { status: "unauthenticated" } | AuthenticatedSession;

export interface SessionStore {
  getSnapshot(): SessionState;
  subscribe(listener: () => void): () => void;
  restore(): void;
  setState(next: AuthenticatedSession): void;
  clear(): void;
}

type StoredRecord = { version: 1; session: AuthenticatedSession };
type StoragePort = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function neutralHouse(id: string): House { return { id, label: `房屋 · ${id.slice(0, 8)}`, resolved: false }; }
export function unresolvedHouses(ids: readonly string[]): House[] { return ids.map(neutralHouse); }
export function resolvedHouse(id: string, building: string, unit: string, roomNo: string): House {
  return { id, building, unit, roomNo, resolved: true, label: `${building} · ${unit} · ${roomNo}` };
}
function isNonEmptyString(value: unknown): value is string { return typeof value === "string" && value.trim().length > 0; }

function parseHouse(value: unknown): House | null {
  if (typeof value !== "object" || value === null) return null;
  const house = value as Record<string, unknown>;
  if (!isNonEmptyString(house.id) || typeof house.resolved !== "boolean" || !isNonEmptyString(house.label)) return null;
  if (!house.resolved) return neutralHouse(house.id);
  if (!isNonEmptyString(house.building) || !isNonEmptyString(house.unit) || !isNonEmptyString(house.roomNo)) return null;
  return resolvedHouse(house.id, house.building, house.unit, house.roomNo);
}

function parseSession(value: unknown): AuthenticatedSession | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const actor = candidate.actor as Record<string, unknown> | undefined;
  if (candidate.status !== "authenticated" || !isNonEmptyString(candidate.accessToken) || !actor) return null;
  if (![actor.id, actor.displayName, actor.communityId, actor.communityName].every(isNonEmptyString)) return null;
  if (!Array.isArray(actor.roles) || !actor.roles.every(isNonEmptyString) || !Array.isArray(candidate.houses)) return null;
  const houses = candidate.houses.map(parseHouse);
  if (houses.some((house) => house === null)) return null;
  const validHouses = houses as House[];
  if (new Set(validHouses.map((house) => house.id)).size !== validHouses.length) return null;
  if (candidate.currentHouseId !== null && !isNonEmptyString(candidate.currentHouseId)) return null;
  if (candidate.currentHouseId && !validHouses.some((house) => house.id === candidate.currentHouseId)) return null;
  return {
    status: "authenticated", accessToken: candidate.accessToken,
    actor: { id: actor.id as string, displayName: actor.displayName as string,
      communityId: actor.communityId as string, communityName: actor.communityName as string,
      roles: [...actor.roles as string[]] },
    houses: validHouses, currentHouseId: candidate.currentHouseId as string | null,
  };
}

function notify(listeners: Set<() => void>): void { listeners.forEach((listener) => listener()); }

export function createInMemorySessionStore(initial: SessionState = { status: "unauthenticated" }): SessionStore {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    restore() { if (state.status === "restoring") { state = { status: "unauthenticated" }; notify(listeners); } },
    setState(next) { state = next; notify(listeners); },
    clear() { state = { status: "unauthenticated" }; notify(listeners); },
  };
}

export function createBrowserSessionStore(storage: StoragePort): SessionStore {
  let state: SessionState = { status: "restoring" };
  const listeners = new Set<() => void>();
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    restore() {
      if (state.status !== "restoring") return;
      try {
        const raw = storage.getItem(browserSessionKey);
        const record = raw ? JSON.parse(raw) as Record<string, unknown> : null;
        const restored = record?.version === browserSessionVersion ? parseSession(record.session) : null;
        if (raw && !restored) storage.removeItem(browserSessionKey);
        state = restored ?? { status: "unauthenticated" };
      } catch {
        try { storage.removeItem(browserSessionKey); } catch { /* Storage may be unavailable. */ }
        state = { status: "unauthenticated" };
      }
      notify(listeners);
    },
    setState(next) {
      const record: StoredRecord = { version: browserSessionVersion, session: next };
      try {
        storage.setItem(browserSessionKey, JSON.stringify(record));
        state = next;
        notify(listeners);
      } catch (error) {
        try { storage.removeItem(browserSessionKey); } catch { /* Storage may be unavailable. */ }
        state = { status: "unauthenticated" };
        notify(listeners);
        throw error;
      }
    },
    clear() {
      try { storage.removeItem(browserSessionKey); } finally {
        state = { status: "unauthenticated" };
        notify(listeners);
      }
    },
  };
}

export type Credentials = { username: string; password: string };
export type HouseSelection = { houseId: string; building: string; unit: string; roomNo: string };
export interface AuthenticationPort {
  signIn(credentials: Credentials): Promise<AuthenticatedSession>;
  selectHouse(houseId: string): Promise<HouseSelection>;
}
