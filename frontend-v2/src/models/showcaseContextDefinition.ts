import { createContext } from "react";
import type { ShowcaseModels } from "./viewModels";

export const ShowcaseContext = createContext<ShowcaseModels | null>(null);
