import { useContext } from "react";
import { ShowcaseContext } from "./showcaseContextDefinition";
import type { ShowcaseModels } from "./viewModels";

export function useShowcaseModels(): ShowcaseModels {
  const value = useContext(ShowcaseContext);
  if (!value) throw new Error("useShowcaseModels must be used inside ShowcaseProvider");
  return value;
}
