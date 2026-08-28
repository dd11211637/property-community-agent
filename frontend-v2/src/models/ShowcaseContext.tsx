import type { ReactNode } from "react";
import type { ShowcaseModels } from "./viewModels";
import { ShowcaseContext } from "./showcaseContextDefinition";

export function ShowcaseProvider({ models, children }: { models: ShowcaseModels; children: ReactNode }) {
  return <ShowcaseContext.Provider value={models}>{children}</ShowcaseContext.Provider>;
}
