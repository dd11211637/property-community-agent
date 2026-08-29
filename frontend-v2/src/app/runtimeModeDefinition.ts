import { createContext, useContext } from "react";

export type RuntimeMode = "real" | "demo";
export const RuntimeModeContext = createContext<RuntimeMode>("real");
export function useRuntimeMode(): RuntimeMode { return useContext(RuntimeModeContext); }
