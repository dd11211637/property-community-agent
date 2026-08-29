import { createRoot } from "react-dom/client";
import { Application } from "../src/app/App";
import { createInMemorySessionStore } from "../src/auth/session";
import "../src/styles/global.css";
import { demoAuthentication } from "./demoAdapters";
import { demoModels } from "./demoData";

const services = {
  sessionStore: createInMemorySessionStore(),
  authentication: demoAuthentication,
  showcaseModels: demoModels,
  mode: "demo" as const,
};

createRoot(document.getElementById("root")!).render(<Application services={services} />);
