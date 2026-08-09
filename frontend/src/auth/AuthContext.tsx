/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { apiRequest } from "../api/client";
import type { House, Session } from "../api/contracts";

type AuthValue = {
  session: Session | null;
  login: (account: string, password: string) => Promise<void>;
  logout: () => void;
  selectHouse: (house: House) => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);
const sessionKey = "property_agent_session";

function storedSession(): Session | null {
  try {
    const value = sessionStorage.getItem(sessionKey);
    return value ? (JSON.parse(value) as Session) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(storedSession);

  const persist = (next: Session | null) => {
    setSession(next);
    if (!next) {
      sessionStorage.removeItem(sessionKey);
      sessionStorage.removeItem("property_agent_token");
      sessionStorage.removeItem("property_agent_house_id");
      return;
    }
    sessionStorage.setItem(sessionKey, JSON.stringify(next));
    sessionStorage.setItem("property_agent_token", next.access_token);
    if (next.current_house_id) sessionStorage.setItem("property_agent_house_id", next.current_house_id);
  };

  const value = useMemo<AuthValue>(
    () => ({
      session,
      login: async (account, password) => {
        const next = await apiRequest<Session>("/api/auth/login", {
          method: "POST",
          body: { account, password },
        });
        persist(next);
      },
      logout: () => persist(null),
      selectHouse: async (house) => {
        if (!session) return;
        await apiRequest("/api/session/current-house", {
          method: "PUT",
          body: { house_id: house.id },
        });
        persist({ ...session, current_house_id: house.id });
      },
    }),
    [session],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
