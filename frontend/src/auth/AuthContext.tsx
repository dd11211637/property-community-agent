/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { apiRequest } from "../api/client";
import type { House, LoginResponse, Session } from "../api/contracts";

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
      sessionStorage.removeItem("property_agent_conversation_id");
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
        const response = await apiRequest<LoginResponse>("/api/auth/login", {
          method: "POST",
          body: { username: account, password },
        });
        const houses = response.house_ids.map((id, index) => ({
          id,
          label: id === response.current_house_id ? "当前房屋" : `可选房屋 ${index + 1}`,
        }));
        const next: Session = {
          access_token: response.access_token,
          actor: {
            id: response.actor_id,
            display_name: response.display_name,
            roles: response.roles,
            community_name: response.community_name,
          },
          houses,
          current_house_id: response.current_house_id,
        };
        persist(next);
      },
      logout: () => persist(null),
      selectHouse: async (house) => {
        if (!session) return;
        const selected = await apiRequest<{ house_id: string; building: string; unit: string; room_no: string }>("/api/auth/house", {
          method: "POST",
          body: { house_id: house.id },
        });
        const houses = session.houses.map((item) => item.id === selected.house_id
          ? { ...item, label: `${selected.building} ${selected.unit}单元 ${selected.room_no}` }
          : item);
        persist({ ...session, houses, current_house_id: selected.house_id });
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

export function useOptionalAuth(): AuthValue | null {
  return useContext(AuthContext);
}
