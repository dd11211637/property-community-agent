import type { ReactNode } from "react";
import { RuntimeModeContext, type RuntimeMode } from "./runtimeModeDefinition";

export function RuntimeModeProvider({ mode, children }: { mode: RuntimeMode; children: ReactNode }) {
  return <RuntimeModeContext.Provider value={mode}>{children}</RuntimeModeContext.Provider>;
}
