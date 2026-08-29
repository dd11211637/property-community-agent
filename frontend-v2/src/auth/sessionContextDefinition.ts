import { createContext } from "react";
import type { Credentials, SessionState } from "./session";

export type SessionContextValue = {
  session: SessionState;
  transitioning: boolean;
  selectionError: string;
  sessionNotice: string;
  signIn(credentials: Credentials): Promise<void>;
  signOut(): Promise<void>;
  selectHouse(houseId: string): Promise<void>;
};
export const SessionContext = createContext<SessionContextValue | null>(null);
